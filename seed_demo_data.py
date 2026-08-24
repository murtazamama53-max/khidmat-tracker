"""
Populates the database with clearly fictional demo data for development
only (blueprint section 43). Run with:

    python seed_demo_data.py

Refuses to run against a database that already has an owner account,
to avoid accidentally polluting real data.
"""
import sys
from datetime import date, timedelta
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models import FeePeriod, Invoice, IncomeSource, Payment, RateHistory, Session, Student, User
from app.services import calculation_engine as calc

app = create_app()

with app.app_context():
    if User.query.first() is not None:
        print("An owner account already exists. Refusing to seed demo data over real data.")
        sys.exit(1)

    owner = User(email="demo@example.com", name="Demo Owner")
    owner.set_password("demo-password-123")
    db.session.add(owner)
    db.session.flush()

    sbhs = IncomeSource(user_id=owner.id, name="SBHS", mode="FIXED_HOURS", category="khidmat")
    sghs = IncomeSource(user_id=owner.id, name="SGHS", mode="EXACT_TIME", category="khidmat")
    db.session.add_all([sbhs, sghs])
    db.session.flush()

    db.session.add_all(
        [
            RateHistory(source_id=sbhs.id, rate=Decimal("250"), effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
            RateHistory(source_id=sbhs.id, rate=Decimal("300"), effective_from=date(2026, 7, 1)),
            RateHistory(source_id=sghs.id, rate=Decimal("250"), effective_from=date(2026, 1, 1)),
        ]
    )
    db.session.flush()

    today = date.today()
    for i in range(10):
        session_date = today - timedelta(days=i * 2)
        rate = Decimal("300") if session_date >= date(2026, 7, 1) else Decimal("250")

        duration = calc.fixed_hours_duration(Decimal("3"))
        earning = calc.calculate_earning(duration, rate)
        db.session.add(
            Session(
                user_id=owner.id,
                source_id=sbhs.id,
                date=session_date,
                mode="FIXED_HOURS",
                duration_minutes=duration.duration_minutes,
                decimal_hours=duration.decimal_hours,
                applied_rate=rate,
                calculated_amount=earning.calculated_amount,
                status="completed",
                raw_input=f"Sbhs(3) [demo]",
            )
        )

        duration2 = calc.exact_time_duration(17, 0, 18, 20)
        earning2 = calc.calculate_earning(duration2, rate)
        db.session.add(
            Session(
                user_id=owner.id,
                source_id=sghs.id,
                date=session_date,
                mode="EXACT_TIME",
                start_time=__import__("datetime").time(17, 0),
                end_time=__import__("datetime").time(18, 20),
                duration_minutes=duration2.duration_minutes,
                decimal_hours=duration2.decimal_hours,
                applied_rate=rate,
                calculated_amount=earning2.calculated_amount,
                status="completed",
                raw_input=f"sghs(5-6:20) [demo]",
            )
        )

    # Tuition demo data -- mirrors the blueprint's own Ahmed example.
    ahmed = Student(user_id=owner.id, name="Ahmed", note="Demo tuition student", active=True)
    db.session.add(ahmed)
    db.session.flush()

    db.session.add_all(
        [
            FeePeriod(student_id=ahmed.id, amount=Decimal("8000"), effective_from=date(2026, 1, 1), effective_to=date(2026, 6, 30)),
            FeePeriod(student_id=ahmed.id, amount=Decimal("10000"), effective_from=date(2026, 7, 1)),
        ]
    )
    db.session.flush()

    invoice = Invoice(
        user_id=owner.id,
        student_id=ahmed.id,
        period_start=today.replace(day=1),
        period_end=today,
        amount=Decimal("10000"),
        due_date=today.replace(day=5) if today.day >= 5 else today,
        status="partial",
    )
    db.session.add(invoice)
    db.session.flush()

    db.session.add(Payment(source_type="invoice", source_id=invoice.id, amount=Decimal("6000"), status="paid"))

    db.session.commit()
    print("Demo data created.")
    print("Log in with: demo@example.com / demo-password-123")
