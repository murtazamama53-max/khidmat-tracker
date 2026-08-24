"""
Tests for app.services.calculation_engine

Covers the critical test cases enumerated in blueprint section 33 / 14.
"""
from decimal import Decimal

import pytest

from app.services.calculation_engine import (
    AmbiguousOvernightError,
    CalculationError,
    calculate_earning,
    calculate_fixed_fee,
    effective_hourly_rate,
    exact_time_duration,
    fixed_hours_duration,
    sum_amounts,
)


def test_450pm_to_630pm_is_100_minutes():
    d = exact_time_duration(16, 50, 18, 30)
    assert d.duration_minutes == 100
    assert d.human_readable == "1h 40m"
    assert d.decimal_hours == Decimal(100) / Decimal(60)


def test_510pm_to_615pm_is_65_minutes():
    d = exact_time_duration(17, 10, 18, 15)
    assert d.duration_minutes == 65
    assert d.human_readable == "1h 05m"


def test_500pm_to_620pm_is_80_minutes():
    d = exact_time_duration(17, 0, 18, 20)
    assert d.duration_minutes == 80
    assert d.human_readable == "1h 20m"


def test_sbhs_7_is_exactly_420_minutes():
    d = fixed_hours_duration(Decimal("7"))
    assert d.duration_minutes == 420
    assert d.decimal_hours == Decimal("7")
    assert d.human_readable == "7h"


def test_zero_duration_rejected_fixed_hours():
    with pytest.raises(CalculationError):
        fixed_hours_duration(Decimal("0"))


def test_negative_duration_rejected_fixed_hours():
    with pytest.raises(CalculationError):
        fixed_hours_duration(Decimal("-3"))


def test_zero_duration_rejected_exact_time_same_start_end():
    with pytest.raises(AmbiguousOvernightError):
        # start == end with no overnight confirmation
        exact_time_duration(9, 0, 9, 0)


def test_overnight_session_requires_confirmation():
    with pytest.raises(AmbiguousOvernightError):
        exact_time_duration(23, 30, 1, 0)


def test_overnight_session_calculates_correctly_when_confirmed():
    d = exact_time_duration(23, 30, 1, 0, is_overnight=True)
    # 23:30 -> 24:00 (30 min) + 00:00 -> 01:00 (60 min) = 90 min
    assert d.duration_minutes == 90
    assert d.human_readable == "1h 30m"


def test_earning_calculation_no_rounding_by_default():
    d = exact_time_duration(16, 50, 18, 30)  # 100 minutes, 1.6666... hours
    result = calculate_earning(d, Decimal("250"))
    assert result.applied_rate == Decimal("250")
    # 100/60 * 250 = 416.666...
    expected = (Decimal(100) / Decimal(60)) * Decimal("250")
    assert result.calculated_amount == expected


def test_earning_calculation_with_rounding_enabled():
    d = exact_time_duration(16, 50, 18, 30)
    result = calculate_earning(d, Decimal("250"), rounding_enabled=True)
    assert result.calculated_amount == Decimal("416.67")


def test_sbhs_7_at_250_equals_1750():
    d = fixed_hours_duration(Decimal("7"))
    result = calculate_earning(d, Decimal("250"))
    assert result.calculated_amount == Decimal("1750")


def test_negative_or_zero_rate_rejected():
    d = fixed_hours_duration(Decimal("7"))
    with pytest.raises(CalculationError):
        calculate_earning(d, Decimal("0"))
    with pytest.raises(CalculationError):
        calculate_earning(d, Decimal("-10"))


def test_fixed_fee_passthrough():
    assert calculate_fixed_fee(Decimal("10000")) == Decimal("10000")


def test_fixed_fee_rejects_negative():
    with pytest.raises(CalculationError):
        calculate_fixed_fee(Decimal("-500"))


def test_sum_amounts():
    total = sum_amounts(Decimal("1750"), Decimal("333.33"), Decimal("10000"))
    assert total == Decimal("12083.33")


def test_effective_hourly_rate_excludes_zero_hours():
    assert effective_hourly_rate(Decimal("1000"), Decimal("0")) is None


def test_effective_hourly_rate_basic():
    rate = effective_hourly_rate(Decimal("2500"), Decimal("10"))
    assert rate == Decimal("250")


def test_combined_input_sbhs_and_sghs_totals():
    # Sbhs(7) & sghs(5-6:20) -> SBHS 7h @250 = 1750; SGHS 80min @250 = 333.33...
    sbhs_duration = fixed_hours_duration(Decimal("7"))
    sbhs_earning = calculate_earning(sbhs_duration, Decimal("250"))

    sghs_duration = exact_time_duration(17, 0, 18, 20)  # 80 minutes
    sghs_earning = calculate_earning(sghs_duration, Decimal("250"))

    assert sghs_duration.duration_minutes == 80
    total = sum_amounts(sbhs_earning.calculated_amount, sghs_earning.calculated_amount)
    expected_sghs = (Decimal(80) / Decimal(60)) * Decimal("250")
    assert total == Decimal("1750") + expected_sghs
