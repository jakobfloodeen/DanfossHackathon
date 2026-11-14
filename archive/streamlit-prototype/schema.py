"""
The attribute list ("schema") per product family.

Every product gets compared on these attributes. To add a new family (e.g. "motor"),
add a new entry to FAMILIES. Nothing else in the code needs to change.

Each attribute has:
  unit      -> the unit we store it in AFTER normalisation (None = text attribute)
  better    -> "higher", "lower" or None (None = no better/worse, just show it)
  numeric   -> True if it can be used for matching / scoring
  hint      -> tells the LLM exactly what to look for (important for accuracy!)
"""

FAMILIES = {
    "pump": {
        "max_displacement": {"unit": "cc/rev", "better": None, "numeric": True,
            "hint": "Maximum geometric displacement per revolution (cm3/rev, cc/rev, in3/rev)."},
        "rated_pressure": {"unit": "bar", "better": "higher", "numeric": True,
            "hint": "Rated / continuous / nominal working pressure. NOT peak or max intermittent pressure."},
        "peak_pressure": {"unit": "bar", "better": "higher", "numeric": True,
            "hint": "Peak / maximum / intermittent pressure. NOT rated/continuous pressure."},
        "max_speed": {"unit": "rpm", "better": "higher", "numeric": True,
            "hint": "Maximum input speed at maximum displacement (rpm, min-1)."},
        "max_flow": {"unit": "l/min", "better": "higher", "numeric": True,
            "hint": "Maximum theoretical output flow (l/min, gpm). Leave null if not stated - we calculate it."},
        "weight": {"unit": "kg", "better": "lower", "numeric": True,
            "hint": "Mass / weight of the pump (kg, lb). Note which variant if several."},
        "control_options": {"unit": None, "better": None, "numeric": False,
            "hint": "Available controls, e.g. pressure compensator, load sensing, electric proportional, torque limiter."},
        "mounting_flange": {"unit": None, "better": None, "numeric": False,
            "hint": "Mounting flange standard, e.g. SAE A/B/C, ISO 3019-2."},
        "shaft_options": {"unit": None, "better": None, "numeric": False,
            "hint": "Input shaft types, e.g. splined SAE B 13T, keyed."},
        "through_drive": {"unit": None, "better": None, "numeric": False,
            "hint": "Whether a through-drive / auxiliary mounting pad is available (yes/no + type)."},
        "dimensions": {"unit": None, "better": None, "numeric": False,
            "hint": "Overall dimensions L x W x H, keep the original text and unit."},
        "model_code_example": {"unit": None, "better": None, "numeric": False,
            "hint": "An example model code / ordering code and what its first positions mean."},
    },
    "valve": {
        "max_flow": {"unit": "l/min", "better": "higher", "numeric": True,
            "hint": "Maximum rated flow per work section or per valve (l/min, gpm). Say which in the snippet."},
        "rated_pressure": {"unit": "bar", "better": "higher", "numeric": True,
            "hint": "Rated / continuous pressure at P port. NOT peak."},
        "peak_pressure": {"unit": "bar", "better": "higher", "numeric": True,
            "hint": "Peak / maximum intermittent pressure."},
        "max_work_sections": {"unit": "count", "better": "higher", "numeric": True,
            "hint": "Maximum number of work sections in one valve stack."},
        "actuation_options": {"unit": None, "better": None, "numeric": False,
            "hint": "Actuation: manual, hydraulic, electro-hydraulic, CAN, on/off, proportional."},
        "compensation": {"unit": None, "better": None, "numeric": False,
            "hint": "Pre-compensated, post-compensated (flow sharing), non-compensated, load sensing."},
        "port_sizes": {"unit": None, "better": None, "numeric": False,
            "hint": "Port sizes / thread types for P, T, A/B."},
        "dimensions": {"unit": None, "better": None, "numeric": False,
            "hint": "Section width / overall dimensions, keep original text and unit."},
        "model_code_example": {"unit": None, "better": None, "numeric": False,
            "hint": "An example model code / ordering code."},
    },
}


def attributes(family: str) -> dict:
    return FAMILIES[family]


def numeric_attributes(family: str) -> list[str]:
    return [a for a, spec in FAMILIES[family].items() if spec["numeric"]]
