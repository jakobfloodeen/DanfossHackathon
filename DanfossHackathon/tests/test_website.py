"""Adapter tests: engine result -> the JSON the website draws. Fake AI only."""
import json
from pathlib import Path

from comparison_engine import interpret
from comparison_engine.website import factor_for, to_website
from test_interpret import COMPX, GOOD, P75, fake

EXAMPLE = json.loads((Path(__file__).parent.parent / "frontend" / "examples" / "compare_response.json")
                     .read_text(encoding="utf-8"))
WEBSITE_STATUSES = {"ok", "conflict", "missing", "outdated", "unverified", "inferred", "converted", "pending", "sample",
                    "na"}   # na = "–": no matching value for this product (not counted as missing)


def cells(w):
    return {(a["id"], key): v for a in w["attributes"] for key, v in a["values"].items()}


def test_same_top_level_shape_as_the_website_example():
    w = to_website(interpret(COMPX, None, use_ai=False))
    assert set(EXAMPLE) <= set(w)
    json.dumps(w)                                         # FastAPI must be able to send it


def test_every_cell_uses_a_website_status():
    w = to_website(interpret(COMPX, None, ask=fake(GOOD)))
    assert {v["status"] for v in cells(w).values()} <= WEBSITE_STATUSES


def test_statuses_are_mapped_honestly():
    w = to_website(interpret(P75, None, use_ai=False, pool="competitors"))
    c = cells(w)
    assert c[("max_speed", COMPX)]["status"] == "conflict"
    assert c[("max_speed", COMPX)]["note"] == "2550 (DEMO_compx.pdf p.4) vs 2200 (DEMO_webshop.txt p.1)"
    assert c[("max_flow", P75)]["status"] == "inferred"                 # calculated by code
    assert c[("weight", COMPX)] == {"value": None, "status": "missing", "sources": [],
                                    "note": "Not stated in DEMO_compx.pdf"}
    assert c[("rated_pressure", COMPX)]["status"] == "converted"        # psi -> bar
    assert c[("rated_pressure", COMPX)]["note"] == "Original: 4,060 psi"
    assert c[("max_displacement", P75)]["status"] == "ok"               # cm3/rev = cc/rev, not a conversion


def test_sources_are_numbered_and_quoted():
    w = to_website(interpret(COMPX, None, use_ai=False),
                   doc_url=lambda file, page: f"/files/{file}#page={page}")
    ids = [s["id"] for s in w["sources"]]
    assert ids == list(range(1, len(ids) + 1))
    used = {n for v in cells(w).values() for n in v.get("sources", [])}
    assert used <= set(ids)
    first = w["sources"][0]
    assert first["url"].startswith("/files/") and '"' in first["doc"]   # exact quotes are shown


def test_pending_products_get_awaiting_extraction_cells():
    w = to_website(interpret(COMPX, None, use_ai=False), pending={"kawasaki-kmp": "values not extracted yet"})
    assert "kawasaki-kmp" in [m["key"] for m in w["matches"]]
    assert all(v["status"] == "pending" for (attr, key), v in cells(w).items() if key == "kawasaki-kmp")
    assert any(f["level"] == "pending" for f in w["flags"])


def test_ai_answer_becomes_insights_with_sources():
    w = to_website(interpret(COMPX, None, ask=fake(GOOD)))
    assert w["matches"][0]["key"] == P75                                 # AI's best match is the first column
    assert w["insights"]["differences"][0]["text"].startswith("Closest match")
    assert all(item["sources"] for group in w["insights"].values() for item in group)
    assert w["confidence"]["level"] == "medium"


def test_no_ai_gives_code_differences_and_low_confidence():
    w = to_website(interpret(COMPX, None, use_ai=False))
    assert w["confidence"]["level"] == "low"
    assert any("Rated pressure" in d["text"] for d in w["insights"]["differences"])
    assert w["flags"][0]["level"] == "warning"


def test_conflict_is_flagged_once_with_its_values():
    w = to_website(interpret(COMPX, None, use_ai=False))
    speed_flags = [f for f in w["flags"] if "Max speed" in f["text"]]
    assert len(speed_flags) == 1 and "2550 rpm" in speed_flags[0]["text"] and "2200 rpm" in speed_flags[0]["text"]


def test_missing_and_na_are_different():
    w = to_website(interpret(COMPX, None, use_ai=False))
    c = cells(w)
    assert c[("weight", COMPX)]["status"] == "missing"            # datasheet does not state it
    assert c[("model_code_example", P75)]["status"] == "na"       # pump 75 simply has no such row


def test_core_rows_have_values_on_both_sides():
    w = to_website(interpret(COMPX, None, use_ai=False))
    core = {a["id"] for a in w["attributes"] if a["core"]}
    assert {"max_displacement", "rated_pressure", "peak_pressure", "control_options"} <= core
    assert "model_code_example" not in core and "weight" not in core


def test_ai_pairs_differently_named_attributes_into_one_row():
    a = json.loads(json.dumps(GOOD))
    # pair the competitor's peak pressure (F3) with pump 75's rated pressure (F10): different names
    a["points"].append({"topic": "Pressure", "candidate": P75, "target_facts": ["F3"], "candidate_facts": ["F10"],
                        "candidate_value_is": "lower", "verdict": "unclear", "why": "350 vs 260 bar, different ratings.",
                        "confidence": "low"})
    w = to_website(interpret(COMPX, None, ask=fake(a)))
    pair = next(r for r in w["attributes"] if r.get("paired"))
    assert pair["label"] == "Pressure" and pair["core"] and pair["unit"] == "bar"
    assert pair["values"][COMPX]["value"] == "350" and pair["values"][P75]["value"] == "260"
    # the paired facts are not shown a second time in their own rows
    assert all(r["values"][COMPX]["value"] != "350" for r in w["attributes"] if r["id"] == "peak_pressure")


def test_ai_text_is_capped():
    a = json.loads(json.dumps(GOOD))
    a["talking_points"] = [{"text": f"Point {i}.", "fact_ids": ["F15"]} for i in "abcde"]
    a["tradeoffs"] = [{"text": f"Trade {i}.", "fact_ids": ["F10"]} for i in "abcd"]
    w = to_website(interpret(COMPX, None, ask=fake(a)))
    assert len(w["insights"]["benefits"]) <= 3 and len(w["insights"]["tradeoffs"]) <= 2
    assert len(w["insights"]["differences"]) <= 3


def test_names_from_the_catalog():
    w = to_website(interpret(COMPX, None, use_ai=False),
                   names={P75: {"name": "S45 75", "manufacturer": "Danfoss", "knownEquivalent": True}})
    m = next(m for m in w["matches"] if m["key"] == P75)
    assert m["name"] == "S45 75" and m["knownEquivalent"]


def test_factor_mapping():
    assert factor_for("rated_pressure") == "pressure"
    assert factor_for("max_flow_per_section") == "flow"
    assert factor_for("work_port_threads") == "ports"
    assert factor_for("something_else") == "options"
