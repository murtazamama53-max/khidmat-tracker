from decimal import Decimal

import pytest

from app import create_app
from app.config import Config, TestConfig
from app.services.date_range import app_today


def _today():
    # Tests must compute "today" the same way the app's own routes do
    # (Asia/Karachi, not the server's local/UTC time) -- otherwise a test
    # that seeds data via an HTTP POST (which resolves "today" via
    # app_today()) and then asserts using raw date.today() can flake for
    # roughly 5 hours out of every 24, whenever UTC and Karachi disagree
    # on the calendar date.
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


def test_khidmat_and_tuition_never_mixed_in_calculations_or_display(client, app):
    """Direct regression for the standing requirement: hourly Khidmat earnings and fixed
    Tuition fees must stay visibly separate everywhere, and tuition must never dilute or
    inflate the Khidmat-only effective hourly rate / hours figures."""
    _seed_full_month(client)

    # A large tuition fee that, if ever mixed into the hourly-rate math, would obviously
    # distort the Rs 250/hr rate -- it must not move it at all.
    client.post("/students/add", data={"name": "BigTuitionStudent"})
    from app.models import Student

    with app.app_context():
        student_id = Student.query.filter_by(name="BigTuitionStudent").first().id
    client.post(f"/students/{student_id}/fee-periods/add", data={"amount": "999999", "effective_from": "2026-01-01"})
    today_iso = _today().isoformat()
    client.post(
        f"/students/{student_id}/invoices/add",
        data={"period_start": today_iso[:8] + "01", "period_end": today_iso},
    )

    r = client.get("/reports?range=this_month")
    text = r.data.decode()
    assert r.status_code == 200
    # The Khidmat-only effective rate must remain exactly 250, unaffected by a
    # tuition fee two orders of magnitude larger than the Khidmat earnings.
    assert "PKR 250" in text
    assert "Khidmat" in text and "Tuition" in text
    assert "Khidmat only" in text  # effective rate / hours explicitly labeled

    r2 = client.get("/")
    dash_text = r2.data.decode()
    assert "Khidmat" in dash_text and "Tuition" in dash_text
    assert "Khidmat Effective Rate" in dash_text and "Khidmat Hours" in dash_text

    csv_text = client.get("/reports/export.csv?range=this_month").data.decode()
    assert "Khidmat Earnings (hourly)" in csv_text
    assert "Tuition Earnings (fixed fees)" in csv_text
    assert "Effective Hourly Rate (Khidmat only),250" in csv_text or "Effective Hourly Rate (Khidmat only),249" in csv_text


def _seed_full_month(client):
    """Sessions + tuition + adjustment all dated today, for range-selector and report tests."""
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS"})
    client.post("/sources/add", data={"name": "sghs", "mode": "EXACT_TIME"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        sghs_id = IncomeSource.query.filter_by(name="sghs").first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})
    client.post("/rates/add", data={"source_id": sghs_id, "rate": "250", "effective_from": "2026-01-01"})

    today_iso = _today().isoformat()
    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7) & sghs(5-6:20)", "date": today_iso})
    preview = r.get_json()
    client.post("/sessions/confirm", json={"date": preview["date"], "raw_text": "Sbhs(7) & sghs(5-6:20)", "items": preview["items"]})

    client.post("/students/add", data={"name": "Ahmed"})
    from app.models import Student

    with client.application.app_context():
        ahmed_id = Student.query.filter_by(name="Ahmed").first().id
    client.post(f"/students/{ahmed_id}/fee-periods/add", data={"amount": "10000", "effective_from": "2026-01-01"})
    month_start = _today().replace(day=1).isoformat()
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": month_start, "period_end": today_iso})

    client.post("/adjustments/add", data={"type": "bonus", "amount": "2750", "reason": "Other activities"})
    return sbhs_id, sghs_id, ahmed_id


def test_critical_acceptance_dataset_khidmat_tuition_other_never_blended(client, app):
    """
    Direct reproduction of the specified critical acceptance dataset:
      Khidmat: Sbhs(7)@250 = 1750.00, sghs(5:00-6:20)@250 = 333.33 -> 8h20m / 2083.33
      Tuition: Ahmed = 10000.00
      Other:   2000.00
      Total:   14083.33
      Khidmat effective rate: 250/hr (khidmat_earnings / khidmat_hours only --
      NOT total_income / total_hours, which would wrongly read ~1690/hr).
    """
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS"})
    client.post("/sources/add", data={"name": "sghs", "mode": "EXACT_TIME"})
    from app.models import IncomeSource

    with app.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        sghs_id = IncomeSource.query.filter_by(name="sghs").first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})
    client.post("/rates/add", data={"source_id": sghs_id, "rate": "250", "effective_from": "2026-01-01"})

    today_iso = _today().isoformat()
    preview = client.post(
        "/sessions/parse-preview", json={"text": "Sbhs(7) & sghs(5-6:20)", "date": today_iso}
    ).get_json()
    client.post(
        "/sessions/confirm",
        json={"date": preview["date"], "raw_text": "Sbhs(7) & sghs(5-6:20)", "items": preview["items"]},
    )

    client.post("/students/add", data={"name": "Ahmed"})
    from app.models import Student

    with app.app_context():
        ahmed_id = Student.query.filter_by(name="Ahmed").first().id
    client.post(f"/students/{ahmed_id}/fee-periods/add", data={"amount": "10000", "effective_from": "2026-01-01"})
    month_start = _today().replace(day=1).isoformat()
    client.post(f"/students/{ahmed_id}/invoices/add", data={"period_start": month_start, "period_end": today_iso})

    client.post("/adjustments/add", data={"type": "bonus", "amount": "2000", "reason": "Other activities"})

    # --- Verify the underlying numbers directly, independent of any template ---
    from app.services import earnings_query as eq
    from app.services.analytics_service import paid_vs_pending
    from app.services.calculation_engine import effective_hourly_rate

    with app.app_context():
        start = _today().replace(day=1)
        end = _today()
        sessions = eq.get_sessions(1, start, end)
        khidmat_earnings = sum((Decimal(s.calculated_amount) for s in sessions), Decimal("0"))
        khidmat_minutes = sum(s.duration_minutes for s in sessions)
        khidmat_hours = Decimal(khidmat_minutes) / Decimal(60)

        invoices = eq.get_invoices_overlapping(1, start, end)
        paid, pending = paid_vs_pending(eq.invoice_payment_facts(invoices))
        tuition_total = paid + pending

        adjustments = eq.get_adjustments(1, start, end)
        other_total = sum(
            (Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in adjustments), Decimal("0")
        )

        total_income = khidmat_earnings + tuition_total + other_total
        rate = effective_hourly_rate(khidmat_earnings, khidmat_hours)

    assert khidmat_minutes == 500  # 420 (Sbhs 7h) + 80 (sghs 5:00-6:20)
    assert khidmat_hours == Decimal("500") / Decimal("60")  # 8h20m = 8.3333... decimal hours
    assert khidmat_earnings == Decimal("2083.33")
    assert tuition_total == Decimal("10000.00")
    assert other_total == Decimal("2000.00")
    assert total_income == Decimal("14083.33")
    # The correct formula (khidmat earnings / khidmat hours) lands on ~250/hr.
    # The WRONG formula the spec explicitly warns against (14083.33 / 8.33)
    # would read ~1690/hr -- assert we are nowhere near that.
    assert Decimal("249.9") < rate < Decimal("250.1")
    wrong_blended_rate = total_income / khidmat_hours
    assert wrong_blended_rate > Decimal("1600")  # confirms the two formulas are genuinely different
    assert abs(rate - wrong_blended_rate) > Decimal("1000")

    # --- Verify the dashboard and reports pages display it correctly too ---
    dash_text = client.get("/").data.decode()
    assert "PKR 14,083" in dash_text  # Total Income
    assert "PKR 2,083" in dash_text  # Khidmat
    assert "PKR 10,000" in dash_text  # Tuition
    assert "PKR 250/hr" in dash_text  # Khidmat Effective Rate, not ~1690/hr
    assert "8.3" in dash_text  # Khidmat Hours ~= 8.33

    report_text = client.get("/reports?range=this_month").data.decode()
    assert "PKR 14,083.33" in report_text
    assert "PKR 2,083.33" in report_text
    assert "PKR 10,000.00" in report_text


def test_dashboard_trend_chart_is_khidmat_only(client, app):
    """The Earnings+Hours trend chart must never blend Tuition/Other into the
    earnings line while the hours line stays Khidmat-only -- that pairing
    would visually imply the combined income came from those hours."""
    _seed_full_month(client)
    r = client.get("/")
    text = r.data.decode()
    assert "Khidmat Earnings &amp; Hours Trend" in text or "Khidmat Earnings & Hours Trend" in text
    assert "Khidmat Earnings (PKR)" in text
    assert "Khidmat Hours" in text

    # The chart_earnings series (Khidmat only) must equal Khidmat earnings for
    # the current month, NOT the combined total including the 10,000 tuition fee.
    import re

    m = re.search(r"const trendCtx[\s\S]*?data: (\[[\d.,\s]*\]),\s*\n\s*borderColor: '#2fd98f'", text)
    assert m, "could not locate the Khidmat earnings dataset in the chart script"
    values = eval(m.group(1))
    assert sum(values) == pytest.approx(2083.33, abs=0.01)  # NOT 12083.33 (which would include tuition)


def test_dashboard_default_range_is_this_month(client):
    _seed_full_month(client)
    r = client.get("/")
    assert r.status_code == 200
    assert b"This Month" in r.data


def test_dashboard_this_year_range_includes_same_data(client):
    _seed_full_month(client)
    r = client.get("/?range=this_year")
    assert r.status_code == 200
    assert b"14,833" in r.data  # 2083.33 + 10000 + 2750, same as this_month since all dated today


def test_dashboard_last_month_range_shows_zero_when_nothing_dated_then(client):
    _seed_full_month(client)
    r = client.get("/?range=last_month")
    assert r.status_code == 200
    # All seeded data is dated "today", so last month should show 0 total earnings.
    assert b"PKR 0" in r.data


def test_dashboard_invalid_range_falls_back_to_this_month(client):
    _seed_full_month(client)
    r = client.get("/?range=nonsense")
    assert r.status_code == 200
    assert b"This Month" in r.data


# ---------------- Calendar card / date filter ----------------


def test_sessions_page_filters_by_date_from_calendar_click(client):
    _seed_full_month(client)
    today_iso = _today().isoformat()
    r = client.get(f"/sessions?date={today_iso}")
    assert r.status_code == 200
    assert b"clear filter" in r.data

    from app.models import Session as SessionModel

    with client.application.app_context():
        expected_count = SessionModel.query.filter_by(date=_today()).count()
    # Two source rows expected (Sbhs + sghs) from the combined quick-add.
    assert expected_count == 2


def test_sessions_page_date_filter_excludes_other_dates(client):
    _seed_full_month(client)
    r = client.get("/sessions?date=2020-01-01")
    assert r.status_code == 200
    assert b"No sessions recorded yet" in r.data or b"clear filter" in r.data


# ---------------- Reports: totals equal ledger ----------------


def test_report_totals_match_actual_ledger_sum(client):
    _seed_full_month(client)
    r = client.get("/reports?range=this_month")
    assert r.status_code == 200
    html = r.data.decode()
    assert "2,083.33" in html or "14,833.33" in html  # source contribution or total shown somewhere
    assert "14,833.33" in html


def test_report_source_filter_narrows_sessions(client):
    sbhs_id, sghs_id, ahmed_id = _seed_full_month(client)
    r = client.get(f"/reports?range=this_month&source_id={sbhs_id}")
    html = r.data.decode()
    assert "Sessions (1)" in html  # only the Sbhs session, not sghs


def test_report_student_filter_narrows_invoices(client):
    sbhs_id, sghs_id, ahmed_id = _seed_full_month(client)
    r = client.get(f"/reports?range=this_month&student_id={ahmed_id}")
    assert b"Tuition Invoices (1)" in r.data


# ---------------- CSV / PDF export ----------------


def test_csv_export_totals_match_report_page(client):
    _seed_full_month(client)
    r = client.get("/reports/export.csv?range=this_month")
    assert r.status_code == 200
    assert r.headers["Content-Type"].startswith("text/csv")
    text = r.data.decode()
    assert "Khidmat Earnings (hourly),2083.33" in text
    assert "Tuition Earnings (fixed fees),10000.00" in text
    assert "Total Earnings (combined),14833.33" in text
    assert "khidmat-report-" in r.headers["Content-Disposition"]


def test_pdf_export_is_valid_pdf_with_matching_total(client):
    _seed_full_month(client)
    r = client.get("/reports/export.pdf?range=this_month")
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.data[:5] == b"%PDF-"

    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(r.data))
    text = reader.pages[0].extract_text()
    assert "14,833.33" in text


def test_pdf_export_reflects_source_filter(client):
    sbhs_id, sghs_id, ahmed_id = _seed_full_month(client)
    r = client.get(f"/reports/export.pdf?range=this_month&source_id={sbhs_id}")
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(r.data))
    text = reader.pages[0].extract_text()
    assert "1,750.00" in text  # only the SBHS 7h session
    assert "333.33" not in text  # sghs session excluded


# ---------------- Goals ----------------


def test_goals_page_with_no_targets_set(client):
    _create_owner(client)
    r = client.get("/goals")
    assert r.status_code == 200
    assert b"No target set" in r.data


def test_setting_and_viewing_goal_progress(client):
    _seed_full_month(client)
    r = client.post(
        "/goals/save",
        data={"monthly_income_target": "50000", "monthly_hours_target": "40", "monthly_sessions_target": "20"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert b"Goals updated" in r.data

    from app.models import Goal

    with client.application.app_context():
        goal = Goal.query.first()
        assert goal.monthly_income_target == Decimal("50000.00")

    r2 = client.get("/goals")
    # 14833.33 / 50000 = 29.7%
    assert b"29.7%" in r2.data


def test_goal_rejects_negative_target(client):
    _create_owner(client)
    r = client.post("/goals/save", data={"monthly_income_target": "-100"}, follow_redirects=True)
    assert b"positive numbers" in r.data

    from app.models import Goal

    with client.application.app_context():
        assert Goal.query.count() == 0


def test_clearing_a_previously_set_goal(client):
    _create_owner(client)
    client.post("/goals/save", data={"monthly_income_target": "50000"})
    client.post("/goals/save", data={"monthly_income_target": ""})

    from app.models import Goal

    with client.application.app_context():
        goal = Goal.query.first()
        assert goal.monthly_income_target is None


# ---------------- Regression: Phase 1/2 features still work ----------------


def test_quick_add_still_produces_correct_blueprint_example(client):
    _create_owner(client)
    client.post("/sources/add", data={"name": "Sbhs", "mode": "FIXED_HOURS"})
    client.post("/sources/add", data={"name": "sghs", "mode": "EXACT_TIME"})
    from app.models import IncomeSource

    with client.application.app_context():
        sbhs_id = IncomeSource.query.filter_by(name="Sbhs").first().id
        sghs_id = IncomeSource.query.filter_by(name="sghs").first().id
    client.post("/rates/add", data={"source_id": sbhs_id, "rate": "250", "effective_from": "2026-01-01"})
    client.post("/rates/add", data={"source_id": sghs_id, "rate": "250", "effective_from": "2026-01-01"})

    r = client.post("/sessions/parse-preview", json={"text": "Sbhs(7) & sghs(5-6:20)", "date": "2026-08-14"})
    preview = r.get_json()
    assert preview["total"] == "2083.33"


def test_guest_isolation_still_holds(client):
    _create_owner(client)
    client.get("/logout")
    r = client.get("/guest")
    assert r.status_code == 200
    for path in ["/", "/sessions", "/rates", "/students", "/reports", "/goals"]:
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["Location"] == "/guest"
