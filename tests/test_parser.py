from app.services.parser import parse_input


def test_sbhs_7_parses_as_fixed_hours():
    result = parse_input("Sbhs(7)")
    assert result.ok
    assert len(result.components) == 1
    c = result.components[0]
    assert c.source_name == "Sbhs"
    assert c.mode == "FIXED_HOURS"
    assert c.quantity_hours == 7.0
    assert not c.needs_confirmation


def test_sghs_range_parses_as_exact_time():
    result = parse_input("sghs(5-6:20)")
    assert result.ok
    c = result.components[0]
    assert c.mode == "EXACT_TIME"
    assert str(c.start) == "17:00"
    assert str(c.end) == "18:20"


def test_sghs_with_minutes_on_both_sides():
    result = parse_input("sghs(5:10-6:15)")
    c = result.components[0]
    assert str(c.start) == "17:10"
    assert str(c.end) == "18:15"


def test_sghs_with_explicit_am_pm():
    result = parse_input("sghs(4:50 PM-6:30 PM)")
    c = result.components[0]
    assert str(c.start) == "16:50"
    assert str(c.end) == "18:30"


def test_combined_input_with_ampersand():
    result = parse_input("Sbhs(7) & sghs(5-6:20)")
    assert result.ok
    assert len(result.components) == 2
    assert result.components[0].source_name == "Sbhs"
    assert result.components[1].source_name == "sghs"


def test_combined_input_with_comma():
    result = parse_input("Sbhs(7), sghs(5-6:20)")
    assert len(result.components) == 2


def test_combined_input_with_semicolon():
    result = parse_input("Sbhs(7); sghs(5-6:20)")
    assert len(result.components) == 2


def test_unknown_source_needs_confirmation():
    result = parse_input("mystery(3)")
    c = result.components[0]
    assert c.needs_confirmation is True
    assert "not a known source" in c.confirmation_reason


def test_empty_input_produces_error():
    result = parse_input("")
    assert not result.ok
    assert result.errors


def test_malformed_component_produces_error_not_crash():
    result = parse_input("this is not valid shorthand")
    assert not result.ok
    assert result.errors
