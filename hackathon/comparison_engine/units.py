"""
UNITS - numbers and units are converted in code, never by the AI.

    parse_number("4,33")          -> 4.33      (European decimal comma)
    parse_number("1,450")         -> 1450.0    (thousands separator: comma + exactly 3 digits)
    parse_number("PC, LS")        -> None      (text is never turned into a number)
    normalize("4,33", "in3/rev")  -> Normalized(70.956, "cc/rev", "")
    normalize("12", "furlong")    -> Normalized(None, None, "unknown unit")

We always keep the ORIGINAL value + unit next to the normalised one (see repository.py).
"""
import re
from dataclasses import dataclass

# unit as written (lower case, no spaces) -> (dimension, factor to the base unit)
UNITS = {
    # pressure -> bar
    "bar": ("pressure", 1), "psi": ("pressure", 0.0689476), "mpa": ("pressure", 10), "kpa": ("pressure", 0.01),
    # flow -> l/min
    "l/min": ("flow", 1), "lpm": ("flow", 1), "gpm": ("flow", 3.78541), "usgpm": ("flow", 3.78541),
    # displacement -> cc/rev  (cc = cm3)
    "cc/rev": ("displacement", 1), "cm3/rev": ("displacement", 1), "cm3": ("displacement", 1),
    "cc": ("displacement", 1), "ccm": ("displacement", 1), "ccm/rev": ("displacement", 1),
    "in3/rev": ("displacement", 16.3871), "in3": ("displacement", 16.3871), "cir": ("displacement", 16.3871),
    # speed -> rpm  (min-1 = 1/min = rpm)
    "rpm": ("speed", 1), "min-1": ("speed", 1), "1/min": ("speed", 1), "r/min": ("speed", 1),
    # mass -> kg
    "kg": ("mass", 1), "lb": ("mass", 0.453592), "lbs": ("mass", 0.453592), "g": ("mass", 0.001),
    # length -> mm
    "mm": ("length", 1), "cm": ("length", 10), "m": ("length", 1000), "in": ("length", 25.4),
    "inch": ("length", 25.4),
    # counts
    "count": ("count", 1), "pcs": ("count", 1), "sections": ("count", 1),
}
BASE_UNIT = {"pressure": "bar", "flow": "l/min", "displacement": "cc/rev", "speed": "rpm",
             "mass": "kg", "length": "mm", "count": ""}

# a plain number: optional minus, digits, optional , or . separators. Nothing else.
_PLAIN_NUMBER = re.compile(r"-?\d[\d.,]*")


@dataclass
class Normalized:
    value: float | None      # number in the base unit, None if it can't be converted safely
    unit: str | None         # base unit, "" for unit-less numbers, None for text
    problem: str             # "" / "text value" / "unknown unit" / "empty"


def unit_key(unit) -> str:
    """'cm³/rev' -> 'cm3/rev', ' Min⁻¹ ' -> 'min-1'. Empty/None -> ''."""
    if unit is None or (isinstance(unit, float) and unit != unit):   # None or NaN
        return ""
    s = str(unit).strip().lower().replace(" ", "")
    return s.replace("³", "3").replace("⁻¹", "-1").replace("−", "-").replace("–", "-")


def is_known_unit(unit) -> bool:
    return unit_key(unit) in UNITS


def is_empty(value) -> bool:
    return value is None or (isinstance(value, float) and value != value) or str(value).strip() == ""


def parse_number(value) -> float | None:
    """Turn ONE plain number into a float. Anything with letters, ranges ('0-2500') or
    several numbers ('2200/2550') is text -> None. We never pick 'the first number' out of text."""
    if is_empty(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "")
    if not _PLAIN_NUMBER.fullmatch(s):
        return None
    if re.fullmatch(r"-?\d{1,3}(,\d{3})+(\.\d+)?", s):     # 1,450 or 1,450.5 -> thousands separator
        s = s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+,\d+", s):      # 1.450,5 -> European thousands + decimal
        s = s.replace(".", "").replace(",", ".")
    elif s.count(",") == 1 and "." not in s:               # 4,33 -> decimal comma
        s = s.replace(",", ".")
    elif "," in s:                                          # anything else with commas is ambiguous
        return None
    try:
        return float(s)
    except ValueError:
        return None


def is_text_value(value) -> bool:
    """True for model codes, option lists, ranges ... anything that is not ONE plain number."""
    return not is_empty(value) and parse_number(value) is None


def normalize(value, unit) -> Normalized:
    """Convert value+unit to the base unit. Never guesses: if unsure -> value None + a problem."""
    if is_empty(value):
        return Normalized(None, None, "empty")
    num = parse_number(value)
    if num is None:
        return Normalized(None, None, "text value")
    key = unit_key(unit)
    if key == "":
        return Normalized(num, "", "")                      # e.g. number of sections
    if key not in UNITS:
        return Normalized(None, None, "unknown unit")
    dim, factor = UNITS[key]
    return Normalized(round(num * factor, 3), BASE_UNIT[dim], "")


def fmt(v: float) -> str:
    """Readable number: 71 instead of 70.956, 280 instead of 279.927, 4.3 instead of 4.33."""
    return f"{round(v):d}" if abs(v) >= 100 else f"{round(v, 1):g}"
