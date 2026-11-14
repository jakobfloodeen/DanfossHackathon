"""System comparison (pump + valve): virtual products, system values calculated by code."""
import shutil
from pathlib import Path

import pytest

from comparison_engine.system import System, build_system_data, compare_systems, compose_system
from comparison_engine.website import label_for, to_website

APP = Path(__file__).resolve().parent.parent
DANFOSS = System("system:danfoss", "Danfoss system", "Danfoss", {"pump": "Danfoss|DEMO pump 75", "valve": "pvg32"})


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    """Demo pumps + the shipped valve facts in one temporary repository."""
    r = tmp_path / "repo"
    r.mkdir()
    for f in list((APP / "data" / "repository").glob("*.csv")) + [
            APP / "examples/repository_demo/danfoss_DEMO_pumps.csv",
            APP / "examples/repository_demo/competitorx_DEMO_pump71.csv",
            APP / "examples/repository_demo/webshop_DEMO_compx71.csv"]:
        shutil.copy(f, r / f.name)
    monkeypatch.setenv("REPO_DIR", str(r))


def rows_of(data, system_id, attribute):
    return data[(data["product_id"] == system_id) & (data["attribute"] == attribute)]


def test_component_facts_keep_their_source():
    data = build_system_data([DANFOSS])
    r = rows_of(data, "system:danfoss", "valve__max_inlet_flow").iloc[0]
    assert r["norm_value"] == 140 and r["source_file"] == "BC152886483664en-001602.pdf" and r["page"] == 12
    assert not rows_of(data, "system:danfoss", "pump__max_displacement").empty


def test_system_values_are_calculated_by_code():
    data = build_system_data([DANFOSS])
    flow = rows_of(data, "system:danfoss", "system__max_flow").iloc[0]
    assert flow["norm_value"] == 140 and flow["status"] == "calculated"          # min(180 pump, 140 valve)
    assert flow["quote"] == "min(pump.max_flow, valve.max_inlet_flow) = 140 l/min"
    ratio = rows_of(data, "system:danfoss", "system__pump_to_valve_flow_ratio").iloc[0]
    assert ratio["norm_value"] == pytest.approx(1.29)


def test_missing_input_is_never_guessed():
    data = build_system_data([DANFOSS])
    p = rows_of(data, "system:danfoss", "system__max_pressure_continuous").iloc[0]
    assert p["status"] == "missing" and "cannot calculate: valve.max_pressure_continuous not stated" in p["quote"]


def test_conflicting_input_blocks_the_system_value():
    comp = System("system:c", "C", "Competitors", {"pump": "CompetitorX|DEMO pump 71", "valve": "kawasaki-kmp"})
    data = build_system_data([comp])
    assert "not stated" in rows_of(data, "system:c", "system__max_flow").iloc[0]["quote"]   # pump flow: speed conflict


def test_compose_fills_open_components():
    c = compose_system("system:competitor", "Competitor system", "Competitors", {"pump": None, "valve": None},
                       DANFOSS, "competitors")
    assert c.components["pump"] == "CompetitorX|DEMO pump 71"
    assert c.components["valve"] and not c.components["valve"].lower().startswith("danfoss")   # a competitor valve
    assert all("automatically" in n for n in c.notes)


def test_system_comparison_end_to_end_without_ai():
    c = compose_system("system:competitor", "Competitor system", "Competitors", {"pump": None, "valve": None},
                       DANFOSS, "competitors")
    r = compare_systems(DANFOSS, c, use_ai=False)
    assert r.status == "no_ai" and r.best_match_id == "system:competitor"
    calc = next(f for f in r.facts if f.attribute == "system__max_flow" and f.product_id == "system:danfoss")
    assert len(calc.based_on) == 2 and all(b.startswith("F") for b in calc.based_on)   # cites component facts
    w = to_website(r)
    core = [a["label"] for a in w["attributes"] if a["core"]]
    assert "System · Max flow" in core and "System · Pump to valve flow ratio" in core
    assert any("cannot calculate" in f["text"] for f in w["flags"])


def test_labels_for_component_facts():
    assert label_for("pump__max_flow") == "Pump · Max flow"
    assert label_for("system__pump_to_valve_flow_ratio") == "System · Pump to valve flow ratio"


def _sys_fact(pid, attr, value, unit="bar"):
    import pandas as pd
    return {"product_id": pid, "brand": "B", "product_name": pid, "family": "x", "attribute": attr,
            "attribute_original": "", "value": str(value), "unit": unit, "norm_value": float(value),
            "norm_unit": unit, "source_file": "x.pdf", "page": 1, "csv_file": "x.csv", "csv_line": 1,
            "quote": "q", "status": "ok", "condition": "", "note": "", "based_on": [], "role": ""}


def test_nary_pressure_chain_uses_only_the_components_the_system_has():
    import pandas as pd
    from comparison_engine.system import System, system_values
    rules = [{"name": "max_pressure_continuous", "op": "min",
              "inputs": ["pump.rated_pressure", "valve.max_pressure_continuous", "motor.rated_pressure"]}]
    s = System("system:x", "X", "B", {"pump": "p1", "valve": "v1"})          # no motor in this system
    rows = pd.DataFrame([_sys_fact("system:x", "pump__rated_pressure", 310),
                         _sys_fact("system:x", "valve__max_pressure_continuous", 350)])
    out = system_values(s, rows, rules)
    assert out.iloc[0]["norm_value"] == 310                                  # min over the two present
    assert "motor" not in out.iloc[0]["quote"]                              # absent component not in formula

    s3 = System("system:y", "Y", "B", {"pump": "p1", "valve": "v1", "motor": "m1"})
    rows3 = pd.DataFrame([_sys_fact("system:y", "pump__rated_pressure", 310),
                          _sys_fact("system:y", "valve__max_pressure_continuous", 350),
                          _sys_fact("system:y", "motor__rated_pressure", 250)])
    assert system_values(s3, rows3, rules).iloc[0]["norm_value"] == 250


def test_scaled_ratio_gives_motor_speed_in_rpm():
    import pandas as pd
    from comparison_engine.system import System, system_values
    rules = [{"name": "motor_speed_at_full_flow", "op": "ratio", "scale": 1000, "unit": "rpm",
              "inputs": ["pump.max_flow", "motor.max_displacement"]}]
    s = System("system:x", "X", "B", {"pump": "p1", "motor": "m1"})
    rows = pd.DataFrame([_sys_fact("system:x", "pump__max_flow", 120, "l/min"),
                         _sys_fact("system:x", "motor__max_displacement", 50, "cm3")])
    out = system_values(s, rows, rules)
    assert out.iloc[0]["norm_value"] == 2400.0 and out.iloc[0]["norm_unit"] == "rpm"


def test_rule_for_an_absent_component_is_skipped_not_reported_missing():
    import pandas as pd
    from comparison_engine.system import System, system_values
    rules = [{"name": "steering_share_of_pump_flow", "op": "ratio",
              "inputs": ["steering.max_flow", "pump.max_flow"]}]
    s = System("system:x", "X", "B", {"pump": "p1", "valve": "v1"})          # no steering
    rows = pd.DataFrame([_sys_fact("system:x", "pump__max_flow", 120, "l/min")])
    assert system_values(s, rows, rules).empty                               # no 'cannot calculate' noise


def test_all_family_rule_files_load():
    from comparison_engine.logic import load_logic
    for family in ("valve", "pump", "motor", "steering", "system"):
        logic = load_logic(family)
        assert logic.settings.get("weights"), family
    assert len(load_logic("system").settings["system_rules"]) >= 5
