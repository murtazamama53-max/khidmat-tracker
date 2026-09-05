import csv
import io
from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, current_app, render_template, request, session

from app.models import IncomeSource, Student
from app.routes.auth import owner_only
from app.services import earnings_query as eq
from app.services.analytics_service import (
    average_session_duration_minutes,
    paid_vs_pending,
    source_contribution,
)
from app.services.calculation_engine import effective_hourly_rate
from app.services.date_range import InvalidRangeError, app_today, resolve_range

bp = Blueprint("reports", __name__)


def _parse_date_arg(name):
    raw = request.args.get(name)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _gather_report_data(user_id: int):
    """Shared by the HTML report page and both export endpoints, so a report and its export always agree."""
    today = app_today(current_app.config["TIMEZONE"])
    range_key = request.args.get("range", "this_month")
    if range_key not in ("this_month", "last_month", "this_year", "custom"):
        range_key = "this_month"

    try:
        start, end, range_label = resolve_range(
            range_key, today, custom_start=_parse_date_arg("start"), custom_end=_parse_date_arg("end")
        )
    except InvalidRangeError:
        start, end, range_label = resolve_range("this_month", today)
        range_key = "this_month"

    source_id = request.args.get("source_id", type=int)
    student_id = request.args.get("student_id", type=int)

    sessions = eq.get_sessions(user_id, start, end)
    if source_id:
        sessions = [s for s in sessions if s.source_id == source_id]

    invoices = eq.get_invoices_overlapping(user_id, start, end)
    if student_id:
        invoices = [i for i in invoices if i.student_id == student_id]

    adjustments = eq.get_adjustments(user_id, start, end)

    khidmat_earnings = sum((Decimal(s.calculated_amount) for s in sessions), Decimal("0"))
    total_minutes = sum(s.duration_minutes for s in sessions)
    total_hours = Decimal(total_minutes) / Decimal(60)
    rate = effective_hourly_rate(khidmat_earnings, total_hours)

    tuition_facts = eq.invoice_payment_facts(invoices)
    tuition_paid, tuition_pending = paid_vs_pending(tuition_facts)

    adjustments_net = sum(
        (Decimal(a.amount) if a.type == "bonus" else -Decimal(a.amount) for a in adjustments), Decimal("0")
    )

    tuition_expected = tuition_paid + tuition_pending
    total_earnings = khidmat_earnings + tuition_paid + tuition_pending + adjustments_net
    total_paid = khidmat_earnings + tuition_paid + adjustments_net
    total_pending = tuition_pending

    avg_duration = average_session_duration_minutes(eq.to_session_facts(sessions))
    by_source = source_contribution(eq.to_earning_events(sessions, invoices, adjustments))

    return {
        "range_key": range_key,
        "range_label": range_label,
        "start": start,
        "end": end,
        "source_id": source_id,
        "student_id": student_id,
        "sessions": sessions,
        "invoices": invoices,
        "adjustments": adjustments,
        # Kept separate end-to-end (never summed into one bucket before
        # display) so hourly Khidmat earnings and fixed Tuition income are
        # always distinguishable, even though "total_earnings" below also
        # gives the combined figure at a glance.
        "khidmat_earnings": khidmat_earnings,
        "tuition_earnings": tuition_expected,
        "adjustments_net": adjustments_net,
        "total_earnings": total_earnings,
        "total_paid": total_paid,
        "total_pending": total_pending,
        "total_hours": total_hours,
        "session_count": len(sessions),
        "effective_rate": rate,
        "avg_duration_minutes": avg_duration,
        "by_source": by_source,
    }


@bp.route("/reports")
@owner_only
def index():
    user_id = session["user_id"]
    data = _gather_report_data(user_id)
    sources = IncomeSource.query.filter_by(user_id=user_id).all()
    students = Student.query.filter_by(user_id=user_id).all()
    return render_template("reports.html", sources=sources, students=students, **data)


@bp.route("/reports/export.csv")
@owner_only
def export_csv():
    user_id = session["user_id"]
    data = _gather_report_data(user_id)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Murtaza -- Khidmat Earnings Tracker: Report"])
    writer.writerow([f"Period: {data['range_label']} ({data['start']} to {data['end']})"])
    writer.writerow([])
    writer.writerow(["Khidmat Earnings (hourly)", str(data["khidmat_earnings"])])
    writer.writerow(["Tuition Earnings (fixed fees)", str(data["tuition_earnings"])])
    if data["adjustments_net"] != 0:
        writer.writerow(["Other / Adjustments", str(data["adjustments_net"])])
    writer.writerow(["Total Earnings (combined)", str(data["total_earnings"])])
    writer.writerow(["Paid", str(data["total_paid"])])
    writer.writerow(["Pending", str(data["total_pending"])])
    writer.writerow(["Hours (Khidmat only)", str(data["total_hours"])])
    writer.writerow(["Sessions", str(data["session_count"])])
    writer.writerow(["Effective Hourly Rate (Khidmat only)", str(data["effective_rate"]) if data["effective_rate"] is not None else "N/A"])
    writer.writerow([])
    writer.writerow(["Date", "Source", "Mode", "Duration (min)", "Rate", "Amount", "Status"])
    for s in data["sessions"]:
        writer.writerow([s.date.isoformat(), s.source.name, s.mode, s.duration_minutes, str(s.applied_rate), str(s.calculated_amount), s.status])
    if data["invoices"]:
        writer.writerow([])
        writer.writerow(["Tuition Invoices"])
        writer.writerow(["Period Start", "Period End", "Student", "Amount", "Status"])
        for inv in data["invoices"]:
            writer.writerow([inv.period_start.isoformat(), inv.period_end.isoformat(), inv.student.name, str(inv.amount), inv.status])

    filename = f"khidmat-report-{data['start'].isoformat()}-to-{data['end'].isoformat()}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/reports/export.pdf")
@owner_only
def export_pdf():
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    user_id = session["user_id"]
    data = _gather_report_data(user_id)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title2", parent=styles["Title"], textColor=colors.HexColor("#111B36"))
    subtitle_style = ParagraphStyle("Subtitle", parent=styles["Normal"], textColor=colors.HexColor("#555555"), spaceAfter=14)

    story = [
        Paragraph("Murtaza &mdash; Khidmat Earnings Tracker", title_style),
        Paragraph(f"Report for {data['range_label']}: {data['start'].strftime('%d %b %Y')} &ndash; {data['end'].strftime('%d %b %Y')}", subtitle_style),
    ]

    summary_rows = [
        ["Khidmat Earnings (hourly)", f"PKR {data['khidmat_earnings']:,.2f}"],
        ["Tuition Earnings (fixed fees)", f"PKR {data['tuition_earnings']:,.2f}"],
    ]
    if data["adjustments_net"] != 0:
        summary_rows.append(["Other / Adjustments", f"PKR {data['adjustments_net']:,.2f}"])
    summary_rows += [
        ["Total Earnings (combined)", f"PKR {data['total_earnings']:,.2f}"],
        ["Paid", f"PKR {data['total_paid']:,.2f}"],
        ["Pending", f"PKR {data['total_pending']:,.2f}"],
        ["Hours (Khidmat only)", f"{data['total_hours']:.2f}"],
        ["Sessions", str(data["session_count"])],
        ["Effective Hourly Rate (Khidmat only)", f"PKR {data['effective_rate']:,.2f}" if data["effective_rate"] is not None else "N/A"],
    ]
    summary_table = Table(summary_rows, colWidths=[2.5 * inch, 2.5 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#EEF1FB")),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111B36")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 20))

    story.append(Paragraph("Sessions", styles["Heading2"]))
    if data["sessions"]:
        session_rows = [["Date", "Source", "Duration", "Rate", "Amount"]]
        for s in data["sessions"]:
            hours = s.duration_minutes // 60
            mins = s.duration_minutes % 60
            session_rows.append(
                [
                    s.date.strftime("%d %b %Y"),
                    s.source.name,
                    f"{hours}h {mins:02d}m",
                    f"PKR {s.applied_rate:.2f}/h",
                    f"PKR {s.calculated_amount:,.2f}",
                ]
            )
        session_table = Table(session_rows, colWidths=[1.1 * inch, 1 * inch, 1 * inch, 1.2 * inch, 1.2 * inch])
        session_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111B36")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FC")]),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(session_table)
    else:
        story.append(Paragraph("No sessions in this period.", styles["Normal"]))

    if data["invoices"]:
        story.append(Spacer(1, 20))
        story.append(Paragraph("Tuition Invoices", styles["Heading2"]))
        invoice_rows = [["Period", "Student", "Amount", "Status"]]
        for inv in data["invoices"]:
            invoice_rows.append(
                [
                    f"{inv.period_start.strftime('%d %b')} - {inv.period_end.strftime('%d %b %Y')}",
                    inv.student.name,
                    f"PKR {inv.amount:,.2f}",
                    inv.status.title(),
                ]
            )
        invoice_table = Table(invoice_rows, colWidths=[2 * inch, 1.5 * inch, 1.3 * inch, 1 * inch])
        invoice_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111B36")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FC")]),
                ]
            )
        )
        story.append(invoice_table)

    story.append(Spacer(1, 24))
    story.append(
        Paragraph(
            "Calculation rules: durations are computed to the exact minute; monetary values use exact "
            "decimal arithmetic; every session and invoice retains the rate/fee that was applied at the "
            "time it was saved, so later rate changes never alter figures shown here.",
            ParagraphStyle("Footnote", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#777777")),
        )
    )

    doc.build(story)
    buf.seek(0)

    filename = f"khidmat-report-{data['start'].isoformat()}-to-{data['end'].isoformat()}.pdf"
    return Response(
        buf.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
