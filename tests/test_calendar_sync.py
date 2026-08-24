from datetime import datetime

import pytest

from app.services.calendar_sync import (
    EventParseError,
    ExistingLinkFact,
    MappingRule,
    match_mapping,
    parse_raw_google_event,
    reconcile,
)


def _raw_event(event_id, title, start_iso, end_iso, calendar_id="cal1", status="confirmed", recurring_event_id=None):
    return {
        "id": event_id,
        "summary": title,
        "status": status,
        "start": {"dateTime": start_iso},
        "end": {"dateTime": end_iso},
        "recurringEventId": recurring_event_id,
        "_calendar_id": calendar_id,
    }


SBHS_RULE = MappingRule(calendar_id=None, title_pattern="SBHS", source_id=1)
SGHS_RULE = MappingRule(calendar_id=None, title_pattern="SGHS", source_id=2)


# ---------------- parse_raw_google_event ----------------


def test_parse_event_extracts_fields():
    raw = _raw_event("evt1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00")
    event = parse_raw_google_event(raw)
    assert event.event_id == "evt1"
    assert event.title == "SBHS"
    assert event.start == datetime.fromisoformat("2026-08-14T16:50:00+05:00")
    assert event.end == datetime.fromisoformat("2026-08-14T18:30:00+05:00")
    assert event.is_cancelled is False


def test_parse_all_day_event_raises():
    raw = {"id": "evt2", "summary": "All day thing", "status": "confirmed", "start": {"date": "2026-08-14"}, "end": {"date": "2026-08-15"}}
    with pytest.raises(EventParseError):
        parse_raw_google_event(raw)


def test_parse_cancelled_event():
    raw = _raw_event("evt3", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00", status="cancelled")
    event = parse_raw_google_event(raw)
    assert event.is_cancelled is True


# ---------------- match_mapping ----------------


def test_match_mapping_case_insensitive_substring():
    event = parse_raw_google_event(_raw_event("e1", "sbhs evening session", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"))
    assert match_mapping(event, [SBHS_RULE, SGHS_RULE]) == 1


def test_match_mapping_no_match_returns_none():
    event = parse_raw_google_event(_raw_event("e1", "Dentist appointment", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"))
    assert match_mapping(event, [SBHS_RULE, SGHS_RULE]) is None


def test_match_mapping_respects_calendar_scoped_rule():
    scoped_rule = MappingRule(calendar_id="work-cal", title_pattern="SBHS", source_id=99)
    event = parse_raw_google_event(_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00", calendar_id="personal-cal"))
    assert match_mapping(event, [scoped_rule]) is None  # wrong calendar


# ---------------- reconcile: first sync ----------------


def test_first_sync_imports_matched_events():
    raw_events = [
        _raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"),
        _raw_event("e2", "SGHS", "2026-08-15T17:00:00+05:00", "2026-08-15T18:20:00+05:00"),
    ]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE, SGHS_RULE])
    assert len(plan.to_import) == 2
    assert plan.to_import[0].source_id == 1
    assert plan.to_import[1].source_id == 2
    assert plan.to_update == []
    assert plan.to_mark_deleted == []
    assert plan.to_draft == []


def test_unknown_event_becomes_draft_not_guessed():
    raw_events = [_raw_event("e1", "Random meeting", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00")]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE])
    assert plan.to_import == []
    assert len(plan.to_draft) == 1
    assert "did not match" in plan.to_draft[0].reason


def test_all_day_event_becomes_draft():
    raw_events = [{"id": "e1", "summary": "Conference", "status": "confirmed", "start": {"date": "2026-08-14"}, "end": {"date": "2026-08-15"}, "_calendar_id": "cal1"}]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE])
    assert len(plan.to_draft) == 1
    assert "all-day" in plan.to_draft[0].reason.lower()


def test_multiple_events_mixed_sources_in_one_sync():
    raw_events = [
        _raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"),
        _raw_event("e2", "SGHS", "2026-08-14T19:00:00+05:00", "2026-08-14T20:00:00+05:00"),
        _raw_event("e3", "SBHS", "2026-08-15T16:50:00+05:00", "2026-08-15T18:30:00+05:00"),
        _raw_event("e4", "Random unrelated thing", "2026-08-15T19:00:00+05:00", "2026-08-15T20:00:00+05:00"),
    ]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE, SGHS_RULE])
    assert len(plan.to_import) == 3
    assert len(plan.to_draft) == 1
    sbhs_count = sum(1 for item in plan.to_import if item.source_id == 1)
    sghs_count = sum(1 for item in plan.to_import if item.source_id == 2)
    assert sbhs_count == 2
    assert sghs_count == 1


def test_historical_events_sync_the_same_as_recent_ones():
    raw_events = [_raw_event("e1", "SBHS", "2020-01-01T16:50:00+05:00", "2020-01-01T18:30:00+05:00")]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE])
    assert len(plan.to_import) == 1


# ---------------- reconcile: second sync must not duplicate ----------------


def test_second_sync_of_unchanged_event_is_skipped_not_reimported():
    raw_events = [_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00")]
    existing = [
        ExistingLinkFact(
            calendar_id="cal1", event_id="e1", occurrence_id=None, session_id=42,
            start=datetime.fromisoformat("2026-08-14T16:50:00+05:00"),
            end=datetime.fromisoformat("2026-08-14T18:30:00+05:00"),
            source_deleted=False,
        )
    ]
    plan = reconcile(raw_events, existing_links=existing, mapping_rules=[SBHS_RULE])
    assert plan.to_import == []
    assert len(plan.skipped) == 1


# ---------------- reconcile: event edits ----------------


def test_event_time_change_produces_update_not_new_import():
    raw_events = [_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:15:00+05:00")]  # end changed 6:30 -> 6:15
    existing = [
        ExistingLinkFact(
            calendar_id="cal1", event_id="e1", occurrence_id=None, session_id=42,
            start=datetime.fromisoformat("2026-08-14T16:50:00+05:00"),
            end=datetime.fromisoformat("2026-08-14T18:30:00+05:00"),
            source_deleted=False,
        )
    ]
    plan = reconcile(raw_events, existing_links=existing, mapping_rules=[SBHS_RULE])
    assert plan.to_import == []
    assert len(plan.to_update) == 1
    assert plan.to_update[0].event.end == datetime.fromisoformat("2026-08-14T18:15:00+05:00")
    assert plan.to_update[0].existing.session_id == 42


# ---------------- reconcile: event deletions ----------------


def test_cancelled_event_with_existing_link_marks_deleted_preserves_session():
    raw_events = [_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00", status="cancelled")]
    existing = [
        ExistingLinkFact(
            calendar_id="cal1", event_id="e1", occurrence_id=None, session_id=42,
            start=datetime.fromisoformat("2026-08-14T16:50:00+05:00"),
            end=datetime.fromisoformat("2026-08-14T18:30:00+05:00"),
            source_deleted=False,
        )
    ]
    plan = reconcile(raw_events, existing_links=existing, mapping_rules=[SBHS_RULE])
    assert len(plan.to_mark_deleted) == 1
    assert plan.to_mark_deleted[0].session_id == 42
    # Critically: nothing in the plan destroys the session -- to_mark_deleted only flags it.


def test_already_marked_deleted_event_is_not_reflagged_repeatedly():
    raw_events = [_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00", status="cancelled")]
    existing = [
        ExistingLinkFact(
            calendar_id="cal1", event_id="e1", occurrence_id=None, session_id=42,
            start=datetime.fromisoformat("2026-08-14T16:50:00+05:00"),
            end=datetime.fromisoformat("2026-08-14T18:30:00+05:00"),
            source_deleted=True,  # already flagged in a previous sync
        )
    ]
    plan = reconcile(raw_events, existing_links=existing, mapping_rules=[SBHS_RULE])
    assert plan.to_mark_deleted == []
    assert len(plan.skipped) == 1


def test_cancelled_event_never_seen_before_is_ignored():
    raw_events = [_raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00", status="cancelled")]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE])
    assert plan.to_import == []
    assert plan.to_draft == []
    assert plan.to_mark_deleted == []
    assert len(plan.skipped) == 1


# ---------------- events_found accounting ----------------


def test_events_found_counts_everything():
    raw_events = [
        _raw_event("e1", "SBHS", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"),  # import
        _raw_event("e2", "Unknown", "2026-08-14T16:50:00+05:00", "2026-08-14T18:30:00+05:00"),  # draft
    ]
    plan = reconcile(raw_events, existing_links=[], mapping_rules=[SBHS_RULE])
    assert plan.events_found == 2
