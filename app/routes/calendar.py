import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app import csrf
from app.extensions import db
from app.models import (
    AuditLog,
    CalendarAccount,
    CalendarDraft,
    CalendarLink,
    CalendarMapping,
    IncomeSource,
)
from app.models import Session as SessionModel
from app.models import SyncLog
from app.routes.auth import owner_only
from app.services import calculation_engine as calc
from app.services import google_calendar_client, token_crypto
from app.services.calendar_sync import ExistingLinkFact, MappingRule, reconcile
from app.services.date_range import app_today
from app.services.google_calendar_client import CalendarConfigError, SyncTokenExpiredError
from app.services.rate_service import RatePeriod, RateResolutionError, resolve_rate

bp = Blueprint("calendar", __name__)

# Renew a watch channel once it's within this long of expiring, rather
# than waiting for it to lapse. Piggybacked onto whatever sync actually
# runs next (manual or webhook-triggered) instead of a separate cron job,
# since real usage naturally syncs at least this often.
WATCH_RENEWAL_WINDOW = timedelta(hours=24)


def _google_config():
    return (
        current_app.config.get("GOOGLE_CLIENT_ID", ""),
        current_app.config.get("GOOGLE_CLIENT_SECRET", ""),
        current_app.config.get("GOOGLE_REDIRECT_URI", ""),
    )


def _rate_periods_for_source(source_id: int):
    from app.models import RateHistory

    rows = RateHistory.query.filter_by(source_id=source_id).all()
    return [
        RatePeriod(id=r.id, source_id=r.source_id, rate=Decimal(r.rate), effective_from=r.effective_from, effective_to=r.effective_to)
        for r in rows
    ]


@bp.route("/calendar")
@owner_only
def index():
    user_id = session["user_id"]
    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    mappings = CalendarMapping.query.filter_by(user_id=user_id).all()
    drafts = CalendarDraft.query.filter_by(user_id=user_id, status="pending").order_by(CalendarDraft.event_date.desc()).all()
    recent_syncs = SyncLog.query.filter_by(user_id=user_id).order_by(SyncLog.started_at.desc()).limit(5).all()
    sources = IncomeSource.query.filter_by(user_id=user_id, active=True).all()
    client_id, client_secret, redirect_uri = _google_config()
    is_configured = bool(client_id and client_secret and redirect_uri)
    return render_template(
        "calendar.html",
        account=account,
        mappings=mappings,
        drafts=drafts,
        recent_syncs=recent_syncs,
        sources=sources,
        is_configured=is_configured,
    )


@bp.route("/calendar/connect")
@owner_only
def connect():
    client_id, client_secret, redirect_uri = _google_config()
    try:
        flow = google_calendar_client.build_flow(client_id, client_secret, redirect_uri)
    except CalendarConfigError as e:
        flash(str(e), "error")
        return redirect(url_for("calendar.index"))

    auth_url, state = google_calendar_client.get_authorization_url(flow)
    session["calendar_oauth_state"] = state
    return redirect(auth_url)


@bp.route("/calendar/oauth/callback")
@owner_only
def oauth_callback():
    user_id = session["user_id"]
    client_id, client_secret, redirect_uri = _google_config()

    error = request.args.get("error")
    if error:
        flash(f"Google sign-in was cancelled or failed: {error}", "error")
        return redirect(url_for("calendar.index"))

    try:
        flow = google_calendar_client.build_flow(client_id, client_secret, redirect_uri)
        flow.state = session.get("calendar_oauth_state")
        refresh_token, email = google_calendar_client.exchange_code_for_refresh_token(flow, request.url)
    except CalendarConfigError as e:
        flash(str(e), "error")
        return redirect(url_for("calendar.index"))
    except Exception as e:
        flash(f"Could not complete Google sign-in: {e}", "error")
        return redirect(url_for("calendar.index"))

    encrypted = token_crypto.encrypt_token(refresh_token, current_app.config["SECRET_KEY"])

    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    if account is None:
        account = CalendarAccount(user_id=user_id, calendar_id="primary")
        db.session.add(account)
    account.google_email = email
    account.encrypted_refresh_token = encrypted
    account.connected_at = datetime.now(timezone.utc)
    db.session.flush()

    db.session.add(AuditLog(user_id=user_id, action="calendar_connected", entity_type="calendar_account", entity_id=account.id))
    db.session.commit()

    flash("Google Calendar connected.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/calendar/disconnect", methods=["POST"])
@owner_only
def disconnect():
    user_id = session["user_id"]
    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    if account is not None:
        if account.watch_channel_id and account.watch_resource_id:
            try:
                client_id, client_secret, _ = _google_config()
                refresh_token = token_crypto.decrypt_token(account.encrypted_refresh_token, current_app.config["SECRET_KEY"])
                google_calendar_client.stop_watch_channel(
                    refresh_token, client_id, client_secret, account.watch_channel_id, account.watch_resource_id
                )
            except Exception:
                pass  # best-effort -- Google auto-expires channels anyway; don't block disconnect on this
        db.session.add(AuditLog(user_id=user_id, action="calendar_disconnected", entity_type="calendar_account", entity_id=account.id))
        db.session.delete(account)
        db.session.commit()
        flash("Google Calendar disconnected. Previously imported sessions are kept.", "success")
    return redirect(url_for("calendar.index"))


def _execute_plan(user_id: int, account: CalendarAccount, plan, log: SyncLog):
    """Writes a reconciliation plan to the database. Never touches money math directly -- reuses rate_service + calculation_engine exactly as manual entry does."""
    imported = 0
    for item in plan.to_import:
        source = db.session.get(IncomeSource, item.source_id)
        if source is None or source.user_id != user_id:
            continue

        event_date = item.event.start.date()
        duration_minutes = int((item.event.end - item.event.start).total_seconds() // 60)
        try:
            duration = calc.duration_from_minutes(duration_minutes)
            rate_period = resolve_rate(event_date, _rate_periods_for_source(source.id))
            earning = calc.calculate_earning(duration, rate_period.rate)
        except (calc.CalculationError, RateResolutionError):
            existing_draft = CalendarDraft.query.filter_by(
                user_id=user_id, calendar_id=item.event.calendar_id, event_id=item.event.event_id
            ).first()
            if existing_draft is None:
                db.session.add(
                    CalendarDraft(
                        user_id=user_id,
                        calendar_id=item.event.calendar_id,
                        event_id=item.event.event_id,
                        occurrence_id=item.event.occurrence_id,
                        title=item.event.title,
                        event_date=event_date,
                        start_time=item.event.start.time(),
                        end_time=item.event.end.time(),
                        reason=f"No rate defined for {source.name} covering {event_date.isoformat()}.",
                    )
                )
            continue

        record = SessionModel(
            user_id=user_id,
            source_id=source.id,
            date=event_date,
            mode="EXACT_TIME",
            start_time=item.event.start.time(),
            end_time=item.event.end.time(),
            duration_minutes=duration.duration_minutes,
            decimal_hours=duration.decimal_hours,
            applied_rate=rate_period.rate,
            calculated_amount=earning.calculated_amount,
            status="completed",
            raw_input=f"[Google Calendar] {item.event.title}",
        )
        db.session.add(record)
        db.session.flush()

        db.session.add(
            CalendarLink(
                session_id=record.id,
                provider="google",
                calendar_id=item.event.calendar_id,
                event_id=item.event.event_id,
                occurrence_id=item.event.occurrence_id,
                title=item.event.title,
                synced_at=datetime.now(timezone.utc),
            )
        )
        db.session.add(
            AuditLog(user_id=user_id, action="session_imported", entity_type="session", entity_id=record.id)
        )
        imported += 1

    updated = 0
    for item in plan.to_update:
        link = (
            CalendarLink.query.join(SessionModel)
            .filter(
                CalendarLink.calendar_id == item.existing.calendar_id,
                CalendarLink.event_id == item.existing.event_id,
                CalendarLink.provider == "google",
                SessionModel.user_id == user_id,
            )
            .first()
        )
        if link is None:
            continue
        record = db.session.get(SessionModel, link.session_id)
        if record is None or record.user_id != user_id:
            continue

        event_date = item.event.start.date()
        duration_minutes = int((item.event.end - item.event.start).total_seconds() // 60)
        try:
            duration = calc.duration_from_minutes(duration_minutes)
            rate_period = resolve_rate(event_date, _rate_periods_for_source(record.source_id))
            earning = calc.calculate_earning(duration, rate_period.rate)
        except (calc.CalculationError, RateResolutionError):
            continue

        record.date = event_date
        record.start_time = item.event.start.time()
        record.end_time = item.event.end.time()
        record.duration_minutes = duration.duration_minutes
        record.decimal_hours = duration.decimal_hours
        record.applied_rate = rate_period.rate
        record.calculated_amount = earning.calculated_amount
        link.synced_at = datetime.now(timezone.utc)
        link.title = item.event.title
        db.session.add(AuditLog(user_id=user_id, action="session_edited", entity_type="session", entity_id=record.id))
        updated += 1

    deleted_upstream = 0
    for existing in plan.to_mark_deleted:
        link = (
            CalendarLink.query.join(SessionModel)
            .filter(
                CalendarLink.calendar_id == existing.calendar_id,
                CalendarLink.event_id == existing.event_id,
                CalendarLink.provider == "google",
                SessionModel.user_id == user_id,
            )
            .first()
        )
        if link is not None:
            link.source_deleted = True
            deleted_upstream += 1

    needing_review = 0
    for item in plan.to_draft:
        already = CalendarDraft.query.filter_by(
            user_id=user_id, calendar_id=item.event.calendar_id, event_id=item.event.event_id
        ).first()
        if already is not None:
            continue
        db.session.add(
            CalendarDraft(
                user_id=user_id,
                calendar_id=item.event.calendar_id,
                event_id=item.event.event_id,
                occurrence_id=item.event.occurrence_id,
                title=item.event.title,
                event_date=item.event.start.date() if item.event.start else app_today(current_app.config["TIMEZONE"]),
                start_time=item.event.start.time() if item.event.start else datetime.min.time(),
                end_time=item.event.end.time() if item.event.end else datetime.min.time(),
                reason=item.reason,
            )
        )
        needing_review += 1

    log.events_found = plan.events_found
    log.events_imported = imported
    log.events_updated = updated
    log.events_skipped = len(plan.skipped)
    log.events_needing_review = needing_review
    log.events_deleted_upstream = deleted_upstream


def _fetch_events_for_account(account, client_id, client_secret, refresh_token):
    """
    Incremental sync when we have a stored token; full time-window sync
    otherwise. Falls back to a full sync automatically if Google reports
    the stored token expired (410) -- the only valid recovery per
    Google's own docs.
    """
    if account.sync_token:
        try:
            return google_calendar_client.fetch_events(
                refresh_token, client_id, client_secret, account.calendar_id, sync_token=account.sync_token
            )
        except SyncTokenExpiredError:
            account.sync_token = None  # fall through to a full resync below

    today = app_today(current_app.config["TIMEZONE"])
    time_min = datetime.combine(today - timedelta(days=90), datetime.min.time()).isoformat() + "Z"
    time_max = datetime.combine(today + timedelta(days=30), datetime.min.time()).isoformat() + "Z"
    return google_calendar_client.fetch_events(
        refresh_token, client_id, client_secret, account.calendar_id, time_min=time_min, time_max=time_max
    )


def _ensure_watch_channel_fresh(account, client_id, client_secret, refresh_token):
    """
    Auto-renews the push-notification channel once it's within
    WATCH_RENEWAL_WINDOW of expiring, piggybacked on whatever sync just
    ran (manual or webhook-triggered) rather than a separate cron job.
    Never enables push sync for an account that hasn't explicitly opted
    in via /calendar/watch/enable -- watch_channel_id being unset means
    "never enabled, or explicitly disabled", and this function does
    nothing in that case. A renewal failure here is swallowed rather than
    failing the whole sync; the next sync will simply try again.
    """
    if account.watch_channel_id is None:
        return
    expiration = account.watch_expiration
    if expiration is not None and expiration.tzinfo is None:
        # SQLite (unlike Postgres) round-trips a stored timezone-aware
        # datetime back as naive -- normalize before comparing, or this
        # raises TypeError ("can't compare offset-naive and offset-aware
        # datetimes") the very first time a channel is checked for renewal.
        expiration = expiration.replace(tzinfo=timezone.utc)
    stale = expiration is None or expiration <= datetime.now(timezone.utc) + WATCH_RENEWAL_WINDOW
    if not stale:
        return

    old_channel_id, old_resource_id = account.watch_channel_id, account.watch_resource_id
    try:
        webhook_url = url_for("calendar.webhook", _external=True)
        new_channel_id = str(uuid.uuid4())
        new_channel_token = secrets.token_urlsafe(32)
        resource_id, expiration_ms = google_calendar_client.create_watch_channel(
            refresh_token, client_id, client_secret, account.calendar_id, new_channel_id, new_channel_token, webhook_url
        )
    except Exception:
        return  # renewal is best-effort; don't let it break the sync itself

    account.watch_channel_id = new_channel_id
    account.watch_resource_id = resource_id
    account.watch_channel_token = new_channel_token
    account.watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc) if expiration_ms else None

    if old_channel_id and old_resource_id:
        try:
            google_calendar_client.stop_watch_channel(refresh_token, client_id, client_secret, old_channel_id, old_resource_id)
        except Exception:
            pass  # best-effort cleanup; Google auto-expires old channels regardless


def _run_sync(user_id: int, account: CalendarAccount, trigger: str = "manual") -> SyncLog:
    """
    The single sync entry point, used by both the manual "Sync Now"
    button and the webhook handler. Identical reconciliation logic either
    way -- reconcile()/_execute_plan() don't know or care whether the
    events they're given came from a full or incremental fetch.
    """
    client_id, client_secret, _ = _google_config()
    log = SyncLog(user_id=user_id, started_at=datetime.now(timezone.utc), trigger=trigger)
    db.session.add(log)
    db.session.flush()

    try:
        refresh_token = token_crypto.decrypt_token(account.encrypted_refresh_token, current_app.config["SECRET_KEY"])

        raw_events, next_sync_token = _fetch_events_for_account(account, client_id, client_secret, refresh_token)
        for e in raw_events:
            e["_calendar_id"] = account.calendar_id

        links = (
            CalendarLink.query.join(SessionModel)
            .filter(SessionModel.user_id == user_id, CalendarLink.provider == "google")
            .all()
        )
        existing_links = [
            ExistingLinkFact(
                calendar_id=link.calendar_id,
                event_id=link.event_id,
                occurrence_id=link.occurrence_id,
                session_id=link.session_id,
                start=datetime.combine(link.session.date, link.session.start_time),
                end=datetime.combine(link.session.date, link.session.end_time),
                source_deleted=link.source_deleted,
            )
            for link in links
            if link.session.start_time and link.session.end_time
        ]

        mapping_rows = CalendarMapping.query.filter_by(user_id=user_id, active=True).all()
        mapping_rules = [MappingRule(calendar_id=m.calendar_id, title_pattern=m.title_pattern, source_id=m.source_id) for m in mapping_rows]

        plan = reconcile(raw_events, existing_links, mapping_rules)
        _execute_plan(user_id, account, plan, log)

        account.sync_token = next_sync_token
        account.last_sync_at = datetime.now(timezone.utc)
        _ensure_watch_channel_fresh(account, client_id, client_secret, refresh_token)

        log.status = "success"
        log.ended_at = datetime.now(timezone.utc)
        db.session.add(AuditLog(user_id=user_id, action="calendar_synced", entity_type="sync_log", entity_id=log.id))
        db.session.commit()

    except CalendarConfigError as e:
        log.status = "error"
        log.error_text = str(e)
        log.ended_at = datetime.now(timezone.utc)
        db.session.commit()
    except token_crypto.TokenDecryptionError:
        log.status = "error"
        log.error_text = "Could not access the stored Google connection. Please reconnect."
        log.ended_at = datetime.now(timezone.utc)
        db.session.commit()
    except Exception as e:
        log.status = "error"
        log.error_text = f"Sync failed: {e}"
        log.ended_at = datetime.now(timezone.utc)
        db.session.commit()

    return log


@bp.route("/calendar/sync", methods=["POST"])
@owner_only
def sync_now():
    user_id = session["user_id"]
    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    if account is None:
        flash("Connect Google Calendar first.", "error")
        return redirect(url_for("calendar.index"))

    log = _run_sync(user_id, account, trigger="manual")

    if log.status == "success":
        parts = [f"{log.events_imported} imported"]
        if log.events_updated:
            parts.append(f"{log.events_updated} updated")
        if log.events_needing_review:
            parts.append(f"{log.events_needing_review} need review")
        if log.events_deleted_upstream:
            parts.append(f"{log.events_deleted_upstream} deleted upstream")
        flash("Sync complete: " + ", ".join(parts) + ".", "success")
    else:
        flash(log.error_text or "Sync failed.", "error")

    return redirect(url_for("calendar.index"))


@bp.route("/calendar/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    """
    Google Calendar push notification receiver. A notification never
    contains the changed event itself -- just "something changed on this
    channel" -- so the only correct response is to run an incremental
    sync and let Google's syncToken diff tell us what actually happened.

    Not @owner_only: Google is the caller here, with no session cookie.
    Authenticity is verified via the channel token Google echoes back
    (established when the channel was created), not via login -- exactly
    Google's own documented verification model for push notifications.
    """
    channel_id = request.headers.get("X-Goog-Channel-ID", "")
    channel_token = request.headers.get("X-Goog-Channel-Token", "")
    resource_state = request.headers.get("X-Goog-Resource-State", "")

    if not channel_id or not channel_token:
        return jsonify({"error": "missing verification headers"}), 400

    account = CalendarAccount.query.filter_by(watch_channel_id=channel_id).first()
    if account is None or not account.watch_channel_token or not secrets.compare_digest(channel_token, account.watch_channel_token):
        return jsonify({"error": "unrecognized channel"}), 404

    if resource_state == "sync":
        # Google's initial handshake sent immediately when a channel is
        # created -- not a real change, nothing to do yet.
        return jsonify({"status": "ok"}), 200

    _run_sync(account.user_id, account, trigger="webhook")
    return jsonify({"status": "ok"}), 200


@bp.route("/calendar/watch/enable", methods=["POST"])
@owner_only
def enable_watch():
    user_id = session["user_id"]
    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    if account is None:
        flash("Connect Google Calendar first.", "error")
        return redirect(url_for("calendar.index"))

    webhook_url = url_for("calendar.webhook", _external=True)
    if not webhook_url.startswith("https://"):
        flash("Push sync needs a public HTTPS address, so it won't work from a local dev server -- it'll work once deployed.", "error")
        return redirect(url_for("calendar.index"))

    client_id, client_secret, _ = _google_config()
    try:
        refresh_token = token_crypto.decrypt_token(account.encrypted_refresh_token, current_app.config["SECRET_KEY"])
        channel_id = str(uuid.uuid4())
        channel_token = secrets.token_urlsafe(32)
        resource_id, expiration_ms = google_calendar_client.create_watch_channel(
            refresh_token, client_id, client_secret, account.calendar_id, channel_id, channel_token, webhook_url
        )
        account.watch_channel_id = channel_id
        account.watch_resource_id = resource_id
        account.watch_channel_token = channel_token
        account.watch_expiration = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc) if expiration_ms else None
        db.session.add(AuditLog(user_id=user_id, action="calendar_watch_enabled", entity_type="calendar_account", entity_id=account.id))
        db.session.commit()
        flash("Push sync enabled -- changes on Google Calendar will now sync automatically.", "success")
    except CalendarConfigError as e:
        flash(str(e), "error")
    except token_crypto.TokenDecryptionError:
        flash("Could not access the stored Google connection. Please reconnect.", "error")
    except Exception as e:
        flash(f"Could not enable push sync: {e}", "error")

    return redirect(url_for("calendar.index"))


@bp.route("/calendar/watch/disable", methods=["POST"])
@owner_only
def disable_watch():
    user_id = session["user_id"]
    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    if account is None or account.watch_channel_id is None:
        flash("Push sync isn't enabled.", "error")
        return redirect(url_for("calendar.index"))

    try:
        client_id, client_secret, _ = _google_config()
        refresh_token = token_crypto.decrypt_token(account.encrypted_refresh_token, current_app.config["SECRET_KEY"])
        google_calendar_client.stop_watch_channel(
            refresh_token, client_id, client_secret, account.watch_channel_id, account.watch_resource_id
        )
    except Exception:
        pass  # best-effort -- still clear our own records regardless

    account.watch_channel_id = None
    account.watch_resource_id = None
    account.watch_channel_token = None
    account.watch_expiration = None
    db.session.add(AuditLog(user_id=user_id, action="calendar_watch_disabled", entity_type="calendar_account", entity_id=account.id))
    db.session.commit()
    flash("Push sync disabled.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/calendar/mappings/add", methods=["POST"])
@owner_only
def add_mapping():
    user_id = session["user_id"]
    title_pattern = request.form.get("title_pattern", "").strip()
    source_id = request.form.get("source_id", type=int)
    calendar_id = request.form.get("calendar_id", "").strip() or None

    if not title_pattern or not source_id:
        flash("A title pattern and source are required.", "error")
        return redirect(url_for("calendar.index"))

    source = IncomeSource.query.filter_by(id=source_id, user_id=user_id).first()
    if source is None:
        flash("Invalid source.", "error")
        return redirect(url_for("calendar.index"))

    mapping = CalendarMapping(user_id=user_id, calendar_id=calendar_id, title_pattern=title_pattern, source_id=source_id)
    db.session.add(mapping)
    db.session.flush()
    db.session.add(AuditLog(user_id=user_id, action="calendar_mapping_added", entity_type="calendar_mapping", entity_id=mapping.id))
    db.session.commit()
    flash(f"Mapping rule added: events containing '{title_pattern}' -> {source.name}.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/calendar/mappings/<int:mapping_id>/delete", methods=["POST"])
@owner_only
def delete_mapping(mapping_id):
    user_id = session["user_id"]
    mapping = CalendarMapping.query.filter_by(id=mapping_id, user_id=user_id).first_or_404()
    db.session.delete(mapping)
    db.session.commit()
    flash("Mapping rule removed.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/calendar/drafts/<int:draft_id>/resolve", methods=["POST"])
@owner_only
def resolve_draft(draft_id):
    """Manually map a draft event to a source, creating the session it represents."""
    user_id = session["user_id"]
    draft = CalendarDraft.query.filter_by(id=draft_id, user_id=user_id).first_or_404()
    source_id = request.form.get("source_id", type=int)

    source = IncomeSource.query.filter_by(id=source_id, user_id=user_id).first()
    if source is None:
        flash("Invalid source.", "error")
        return redirect(url_for("calendar.index"))

    start_dt = datetime.combine(draft.event_date, draft.start_time)
    end_dt = datetime.combine(draft.event_date, draft.end_time)
    duration_minutes = int((end_dt - start_dt).total_seconds() // 60)

    try:
        duration = calc.duration_from_minutes(duration_minutes)
        rate_period = resolve_rate(draft.event_date, _rate_periods_for_source(source.id))
        earning = calc.calculate_earning(duration, rate_period.rate)
    except (calc.CalculationError, RateResolutionError) as e:
        flash(str(e), "error")
        return redirect(url_for("calendar.index"))

    record = SessionModel(
        user_id=user_id,
        source_id=source.id,
        date=draft.event_date,
        mode="EXACT_TIME",
        start_time=draft.start_time,
        end_time=draft.end_time,
        duration_minutes=duration.duration_minutes,
        decimal_hours=duration.decimal_hours,
        applied_rate=rate_period.rate,
        calculated_amount=earning.calculated_amount,
        status="completed",
        raw_input=f"[Google Calendar] {draft.title}",
    )
    db.session.add(record)
    db.session.flush()

    db.session.add(
        CalendarLink(
            session_id=record.id,
            provider="google",
            calendar_id=draft.calendar_id,
            event_id=draft.event_id,
            occurrence_id=draft.occurrence_id,
            title=draft.title,
        )
    )
    draft.status = "resolved"
    db.session.add(AuditLog(user_id=user_id, action="calendar_draft_resolved", entity_type="calendar_draft", entity_id=draft.id))
    db.session.commit()
    flash(f"'{draft.title}' mapped to {source.name} and saved.", "success")
    return redirect(url_for("calendar.index"))


@bp.route("/calendar/drafts/<int:draft_id>/ignore", methods=["POST"])
@owner_only
def ignore_draft(draft_id):
    user_id = session["user_id"]
    draft = CalendarDraft.query.filter_by(id=draft_id, user_id=user_id).first_or_404()
    draft.status = "ignored"
    db.session.commit()
    flash("Event ignored.", "success")
    return redirect(url_for("calendar.index"))


# --------------------------------------------------------------------------
# JSON endpoints powering the custom calendar widget on the dashboard.
# Read-only, @owner_only, deliberately separate from the full-page
# calendar.index render so the dashboard can poll them lightly to reflect
# whatever the incremental sync / webhook system last wrote to the DB,
# without re-rendering the whole page.
# --------------------------------------------------------------------------

def _source_color_index(source_id: int) -> int:
    """A stable, arbitrary-but-consistent color slot per source, so e.g.
    SBHS and SGHS are always the same two colors on every render -- not
    hardcoded to specific source names, since sources are user-defined."""
    return source_id % 5


@bp.route("/calendar/api/month")
@owner_only
def api_month():
    import calendar as calendar_module

    user_id = session["user_id"]
    today = app_today(current_app.config["TIMEZONE"])
    try:
        year = int(request.args.get("year", today.year))
        month = int(request.args.get("month", today.month))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "invalid year/month"}), 400

    _, days_in_month = calendar_module.monthrange(year, month)
    from datetime import date as date_cls

    month_start = date_cls(year, month, 1)
    month_end = date_cls(year, month, days_in_month)

    rows = (
        SessionModel.query.join(IncomeSource)
        .filter(SessionModel.user_id == user_id, SessionModel.date >= month_start, SessionModel.date <= month_end)
        .with_entities(SessionModel.date, IncomeSource.id, IncomeSource.name)
        .all()
    )
    days: dict = {}
    for session_date, source_id, source_name in rows:
        day_key = str(session_date.day)
        bucket = days.setdefault(day_key, {})
        entry = bucket.setdefault(source_id, {"source_id": source_id, "source": source_name, "count": 0, "color_index": _source_color_index(source_id)})
        entry["count"] += 1
    days_out = {day: list(sources.values()) for day, sources in days.items()}

    account = CalendarAccount.query.filter_by(user_id=user_id).first()
    needs_review = CalendarDraft.query.filter_by(user_id=user_id, status="pending").count()

    return jsonify({
        "year": year,
        "month": month,
        "days": days_out,
        "sync": {
            "connected": account is not None,
            "last_sync_at": account.last_sync_at.isoformat() if account and account.last_sync_at else None,
            "watch_enabled": bool(account.watch_channel_id) if account else False,
            "needs_review": needs_review,
        },
    })


@bp.route("/calendar/api/day")
@owner_only
def api_day():
    from datetime import date as date_cls

    user_id = session["user_id"]
    raw_date = request.args.get("date", "")
    try:
        day = date_cls.fromisoformat(raw_date)
    except ValueError:
        return jsonify({"error": "invalid date"}), 400

    sessions_today = (
        SessionModel.query.join(IncomeSource)
        .filter(SessionModel.user_id == user_id, SessionModel.date == day)
        .order_by(SessionModel.start_time)
        .all()
    )

    total_amount = sum((Decimal(s.calculated_amount) for s in sessions_today), Decimal("0"))
    total_minutes = sum(s.duration_minutes for s in sessions_today)

    return jsonify({
        "date": day.isoformat(),
        "sessions": [
            {
                "id": s.id,
                "source_id": s.source_id,
                "source": s.source.name,
                "color_index": _source_color_index(s.source_id),
                "start_time": s.start_time.strftime("%H:%M") if s.start_time else None,
                "end_time": s.end_time.strftime("%H:%M") if s.end_time else None,
                "duration_minutes": s.duration_minutes,
                "amount": str(s.calculated_amount),
            }
            for s in sessions_today
        ],
        "total_amount": str(total_amount),
        "total_hours": f"{Decimal(total_minutes) / Decimal(60):.2f}",
        "session_count": len(sessions_today),
    })
