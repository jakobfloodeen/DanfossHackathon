import pytest

from comparison_engine.units import normalize, parse_number, is_text_value, unit_key


@pytest.mark.parametrize("raw, expected", [
    ("4,33", 4.33),          # European decimal comma
    ("1,450", 1450.0),       # thousands separator
    ("4,060", 4060.0),
    ("1.450,5", 1450.5),
    ("2.5", 2.5),
    ("350", 350.0),
    (" 75 ", 75.0),
    (12, 12.0),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["PC, LS", "A71VO-DEMO", "0-2500", "2200/2550", "n/a", "350 bar", "", None])
def test_text_is_never_a_number(raw):
    assert parse_number(raw) is None


def test_is_text_value():
    assert is_text_value("PC, LS")
    assert not is_text_value("4,33")
    assert not is_text_value("")


@pytest.mark.parametrize("value, unit, num, base", [
    ("4060", "psi", 279.93, "bar"),
    ("35", "MPa", 350, "bar"),
    ("10", "gpm", 37.854, "l/min"),
    ("4,33", "in3/rev", 70.956, "cc/rev"),
    ("45", "cm³/rev", 45, "cc/rev"),
    ("45", "cc", 45, "cc/rev"),
    ("2400", "min-1", 2400, "rpm"),
    ("2400", "min⁻¹", 2400, "rpm"),
    ("50", "lb", 22.68, "kg"),
    ("2", "in", 50.8, "mm"),
    ("3", "", 3, ""),
])
def test_normalize(value, unit, num, base):
    n = normalize(value, unit)
    assert n.value == pytest.approx(num, rel=1e-3)
    assert n.unit == base
    assert n.problem == ""


def test_unknown_unit_is_not_guessed():
    n = normalize("12", "furlong")
    assert n.value is None and n.problem == "unknown unit"


def test_text_value_with_unit_stays_text():
    n = normalize("PC, LS", "")
    assert n.value is None and n.problem == "text value"


def test_unit_key():
    assert unit_key(" Cm³ / rev ") == "cm3/rev"
    assert unit_key(None) == ""
