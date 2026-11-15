"""Same quantity under different datasheet names -> one attribute (comparison_engine/attributes.py)."""
import csv

from comparison_engine.attributes import key, parse_range
from comparison_engine.repository import load_repository

SAME = {"hydraulic_fluid": "oil", "fluid": "oil", "maximum": "max", "temp": "temperature"}
IGNORE = {"range", "iso4406"}


def test_keys_ignore_order_synonyms_and_filler():
    assert key("ambient_temperature", SAME, IGNORE) == key("ambient_temperature_range", SAME, IGNORE)
    assert key("oil_temperature_max", SAME, IGNORE) == key("max_hydraulic_fluid_temperature", SAME, IGNORE)
    assert key("max_pressure_port_p", SAME, IGNORE) == key("max_pressure_p_port", SAME, IGNORE)
    # different quantities stay different
    assert key("flow_temperature_compensation_range", SAME, IGNORE) != key("oil_temperature", SAME, IGNORE)
    assert key("max_pressure_continuous", SAME, IGNORE) != key("max_pressure_intermittent", SAME, IGNORE)
    # a direction keeps its order: P->T is not T->P
    assert key("pressure_drop_p_to_t", SAME, IGNORE) != key("pressure_drop_t_to_p", SAME, IGNORE)


def test_parse_range():
    assert parse_range("-35 to +110") == ("-35", "110")
    assert parse_range("12 - 75") == ("12", "75")
    assert parse_range("-30 → 60") == ("-30", "60")
    assert parse_range("350") is None and parse_range("23/19/16") is None


def _write(path, rows):
    cols = ["product_id", "brand", "product_name", "family", "attribute", "value", "unit", "source_file", "page",
            "quote", "status", "condition", "fact_type"]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for pid, attr, value, page in rows:
            w.writerow([pid, "B", pid, "valves", attr, value, "°C", f"{pid}.pdf", page, f"q {value}", "ok", "", "spec"])


def test_ranges_and_separate_min_max_line_up(tmp_path):
    _write(tmp_path / "a.csv", [("pvg16", "ambient_temperature", "-30 to 60", 7),
                                ("pvg16", "oil_temperature_max", "90", 12), ("pvg16", "oil_temperature_min", "-30", 12)])
    _write(tmp_path / "b.csv", [("sb11", "ambient_temperature_range", "-35 to +80", 5),
                                ("sb11", "max_hydraulic_fluid_temperature", "-35 to +110", 5)])
    d = load_repository(tmp_path)
    rows = {(r.product_id, r.attribute): r for r in d.itertuples()}
    assert rows[("sb11", "ambient_temperature")].attribute_original == "ambient_temperature_range"
    assert rows[("sb11", "oil_temperature_max")].norm_value == 110                  # max end of the range
    assert rows[("sb11", "oil_temperature_min")].norm_value == -35
    assert "max end of the stated range" in rows[("sb11", "oil_temperature_max")].note
    assert rows[("sb11", "oil_temperature_max")].page == 5                          # same source page
    assert ("sb11", "hydraulic_fluid_temperature") in rows                          # the range itself is kept
