from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.config import Config, TestConfig
from app.services.date_range import app_today


def _today():
    # Match the app's own timezone-aware "today" (Asia/Karachi), not the
    # server's local/UTC date -- otherwise a test that seeds data via an
    # HTTP POST (which resolves "today" via app_today()) and asserts
    # against a dashboard/report range computed the same way can flake
    # right at a month boundary, whenever UTC and Karachi disagree.
    return app_today(Config.TIMEZONE)


@pytest.fixture
def app():
    return create_app(TestConfig)


@pytest.fixture
def client(app):
    return app.test_client()


def _create_owner(client):
    return client.post(
        "/setup",
        data={"name": "Murtaza", "email": "murtaza@example.com", "password": "testpass123", "confirm": "testpass123"},
    )


def _create_ahmed_with_blueprint_fees(client):
    """Ahmed: Jan-Jun Rs.8,000/month, Jul-Sep Rs.10,000/month (blueprint section 6)."""
    _create_owner(client)
    client.post("/students/add", data={"name": "Ahmed", "note": ""})

    from app.models import Student

    with client.application.app_context():
        ahmed_id = Student.query.filter_by(name="Ahmed").first().id

    client.post(
        f"/students/{ahmed_id}/fee-periods/add",
        data={"amount": "8000", "effective_from": "2026-01-01", "effective_to": "2026-06-30"},
    )
    client.post(f"/students/{ahmed_id}/fee-periods/add", data={"amount": "10000", "effective_from": "2026-07-01"})
    return ahmed_id


# ---------------- Rate editing ----------------


def test_edit_rate_updates_period_without_touching_old_sessions(client):
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})

    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id

    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})

    from app.models import RateHistory

    with client.application.app_context():
        rate_id = RateHistory.query.filter_by(source_id=sbhs_id).first().id

    # Save a session under the original rate.
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-03-01"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with client.application.app_context():
        original_amount = SessionModel.query.first().calculated_amount
        assert original_amount == Decimal("1750.00")

    # Correct a typo in the rate (250 -> 275).
    r2 = client.post(f"/rates/{rate_id}/edit", data={"rate": "275", "effective_from": "2026-01-01"}, follow_redirects=True)
    assert r2.status_code == 200
    assert b"updated" in r2.data.lower()

    with client.application.app_context():
        # Already-saved session keeps its original snapshotted amount.
        assert SessionModel.query.first().calculated_amount == original_amount
        assert RateHistory.query.get(rate_id).rate == Decimal("275.0000")

    # A NEW session should now use the corrected rate.
    r3 = client.post("/sessions/parse-preview", json={"text": "Sbhs(2)", "date": "2026-03-02"})
    preview3 = r3.get_json()
    assert preview3["items"][0]["amount"] == "550.00"  # 2 * 275


def test_edit_rate_rejects_overlap(client):
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id

    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01", "effective_to": "2026-06-30"})
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "300", "effective_from": "2026-07-01"})

    from app.models import RateHistory

    with client.application.app_context():
        first_id = RateHistory.query.filter_by(rate=Decimal("250.0000")).first().id

    # Try to extend the first period into the second's territory.
    r = client.post(f"/rates/{first_id}/edit", data={"rate": "250", "effective_from": "2026-01-01", "effective_to": "2026-08-01"}, follow_redirects=True)
    assert b"overlap" in r.data.lower()


# ---------------- Students & fee periods ----------------


def test_tuition_fee_resolves_by_billing_date_matching_blueprint_example(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)

    r = client.post(
        f"/students/{ahmed_id}/invoices/add",
        data={"period_start": "2026-08-01", "period_end": "2026-08-31", "due_date": "2026-08-05"},
    )
    assert r.status_code == 302

    from app.models import Invoice

    with client.application.app_context():
        invoice = Invoice.query.filter_by(student_id=ahmed_id).first()
        assert invoice.amount == Decimal("10000.00")  # August uses the Jul-Sep fee, not Jan-Jun


def test_january_invoice_uses_original_fee(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    r = client.post(
        f"/students/{ahmed_id}/invoices/add",
        data={"period_start": "2026-02-01", "period_end": "2026-02-28"},
    )
    assert r.status_code == 302

    from app.models import Invoice

    with client.application.app_context():
        invoice = Invoice.query.filter_by(student_id=ahmed_id).first()
        assert invoice.amount == Decimal("8000.00")


def test_later_fee_change_does_not_alter_already_generated_invoice(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": "2026-08-01", "period_end": "2026-08-31"})

    from app.models import Invoice

    with client.application.app_context():
        invoice_id = Invoice.query.filter_by(student_id=ahmed_id).first().id
        before = Invoice.query.get(invoice_id).amount

    # A correction/new fee period added afterwards must not retroactively touch the invoice.
    client.post(f"/students/{ahmed_id}/fee-periods/add", data={"amount": "15000", "effective_from": "2026-10-01"})

    with client.application.app_context():
        after = Invoice.query.get(invoice_id).amount
        assert before == after == Decimal("10000.00")


def test_fee_period_overlap_rejected(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    r = client.post(
        f"/students/{ahmed_id}/fee-periods/add",
        data={"amount": "9000", "effective_from": "2026-06-01", "effective_to": "2026-07-15"},
        follow_redirects=True,
    )
    assert b"overlap" in r.data.lower()


def test_invoice_generation_without_covering_fee_period_is_rejected(client):
    _create_owner(client)
    client.post("/students/add", data={"name": "NoFeeStudent"})
    from app.models import Student

    with client.application.app_context():
        sid = Student.query.filter_by(name="NoFeeStudent").first().id

    r = client.post(f"/students/{sid}/invoices/add", data={"period_start": "2026-08-01", "period_end": "2026-08-31"}, follow_redirects=True)
    assert b"No tuition fee is defined" in r.data

    from app.models import Invoice

    with client.application.app_context():
        assert Invoice.query.filter_by(student_id=sid).count() == 0


# ---------------- Payments ----------------


def test_partial_payment_then_full_payment(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    future_due = (date.today() + timedelta(days=30)).isoformat()
    client.post(
        f"/students/{ahmed_id}/invoices/add",
        data={"period_start": "2026-08-01", "period_end": "2026-08-31", "due_date": future_due},
    )

    from app.models import Invoice

    with client.application.app_context():
        invoice_id = Invoice.query.filter_by(student_id=ahmed_id).first().id

    client.post(f"/invoices/{invoice_id}/payments/add", data={"amount": "6000"})
    with client.application.app_context():
        inv = Invoice.query.get(invoice_id)
        assert inv.status == "partial"

    client.post(f"/invoices/{invoice_id}/payments/add", data={"amount": "4000"})
    with client.application.app_context():
        inv = Invoice.query.get(invoice_id)
        assert inv.status == "paid"


def test_overpayment_rejected(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": "2026-08-01", "period_end": "2026-08-31"})
    from app.models import Invoice

    with client.application.app_context():
        invoice_id = Invoice.query.filter_by(student_id=ahmed_id).first().id

    r = client.post(f"/invoices/{invoice_id}/payments/add", data={"amount": "10001"}, follow_redirects=True)
    assert b"exceeds" in r.data

    with client.application.app_context():
        assert Invoice.query.get(invoice_id).status == "pending"  # or overdue -- but no payment recorded either way


def test_overdue_when_due_date_passed_with_balance(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    past_due = (date.today() - timedelta(days=5)).isoformat()
    client.post(
        f"/students/{ahmed_id}/invoices/add",
        data={"period_start": "2026-08-01", "period_end": "2026-08-31", "due_date": past_due},
    )
    from app.models import Invoice

    with client.application.app_context():
        invoice_id = Invoice.query.filter_by(student_id=ahmed_id).first().id

    client.post(f"/invoices/{invoice_id}/payments/add", data={"amount": "3000"})
    with client.application.app_context():
        assert Invoice.query.get(invoice_id).status == "overdue"


def test_pending_tuition_has_zero_paid(client):
    ahmed_id = _create_ahmed_with_blueprint_fees(client)
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": "2026-08-01", "period_end": "2026-08-31"})
    from app.models import Invoice

    with client.application.app_context():
        inv = Invoice.query.filter_by(student_id=ahmed_id).first()
        assert inv.status == "pending"


# ---------------- Adjustments ----------------


def test_adjustment_requires_reason(client):
    _create_owner(client)
    r = client.post("/adjustments/add", data={"type": "bonus", "amount": "100", "reason": ""}, follow_redirects=True)
    assert b"requires a reason" in r.data.lower()

    from app.models import Adjustment

    with client.application.app_context():
        assert Adjustment.query.count() == 0


def test_standalone_bonus_counts_as_other_income_on_dashboard(client):
    _create_owner(client)
    client.post("/adjustments/add", data={"type": "bonus", "amount": "2750", "reason": "Eid gift"})

    r = client.get("/")
    assert b"2,750.00" in r.data


def test_adjustment_never_mutates_session_amount(client):
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})

    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7)", "date": "2026-08-14"})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7)", "items": preview["items"]})

    from app.models import Session as SessionModel

    with client.application.app_context():
        session_id = SessionModel.query.first().id
        original_amount = SessionModel.query.first().calculated_amount

    client.post("/adjustments/add", data={"type": "bonus", "amount": "100", "reason": "Extended session", "session_id": str(session_id)})

    with client.application.app_context():
        # The session's own calculated_amount is untouched -- the bonus is a separate ledger line.
        assert SessionModel.query.get(session_id).calculated_amount == original_amount


# ---------------- Combined dashboard totals ----------------


def test_combined_monthly_totals_khidmat_tuition_and_adjustments(client):
    """Mirrors blueprint section 6's combined earnings view example."""
    ahmed_id = _create_ahmed_with_blueprint_fees(client)

    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS", "category": "khidmat"})
    client.post("/sources/add", data={"name": "sghs", "mode": "EXACT_TIME", "category": "khidmat"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        sghs_id = IncomeSource.query.filter_by(name="sghs").first().id

    today_iso = _today().isoformat()
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})
    client.post("/rates/add", data={"source_id": sghs_id, "rate": "250", "effective_from": "2026-01-01"})

    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7) & sghs(5-6:20)", "date": today_iso})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7) & sghs(5-6:20)", "items": preview["items"]})

    period_start = _today().replace(day=1).isoformat()
    period_end = _today().isoformat()
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": period_start, "period_end": period_end})

    client.post("/adjustments/add", data={"type": "bonus", "amount": "2750", "reason": "Other activities"})

    r2 = client.get("/")
    html = r2.data.decode()
    # Khidmat 2083.33 + Tuition 10000 + Other 2750 = 14833.33
    # The breakdown table shows exact 2-decimal amounts per category...
    assert "2,083.33" in html
    assert "10,000.00" in html
    assert "2,750.00" in html
    # ...while the Total Earnings KPI card intentionally rounds to whole PKR
    # (matching the blueprint's own UI reference), so check the rounded total.
    assert "14,833" in html
