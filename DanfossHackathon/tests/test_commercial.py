"""Approved commercial notes: the review queue, the engine integration and the role gating."""
import pandas as pd
import pytest

from comparison_engine import commercial
from comparison_engine.interpret import interpret

EDITOR = {"id": "senior", "name": "Senior", "role": "editor"}
VIEWER = {"id": "junior", "name": "Junior", "role": "viewer"}


def _seed(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMERCIAL_FILE", str(tmp_path / "commercial.csv"))
    commercial.add("pvg32", "positioning", "Go-to product in the PVG platform.", EDITOR,
                   "02 Spool Valves 2026 Q1 Sales Guidelines", "6")
    commercial.add("valve", "transition", "PVG120 no longer promoted - transition to PVG48 or PVG128.", EDITOR,
                   "02 Spool Valves 2026 Q1 Sales Guidelines", "25")


def test_editor_notes_go_live_viewer_notes_wait(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    suggestion = commercial.add("pvg32", "selling_point", "Customer praise in loader demos.", VIEWER)
    assert suggestion["status"] == "pending"
    assert [r["id"] for r in commercial.for_products(["pvg32"])] == ["COM-001"]        # pending not served
    assert commercial.for_products(["pvg32"], status="pending")[0]["id"] == suggestion["id"]
    with pytest.raises(PermissionError):
        commercial.decide(suggestion["id"], "approve", VIEWER)
    commercial.decide(suggestion["id"], "approve", EDITOR)
    assert len(commercial.for_products(["pvg32"])) == 2                                # now it is served


def test_family_notes_attach_and_order_target_first(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    rows = commercial.for_products(["pvg32"], family="valve")
    assert [r["product_id"] for r in rows] == ["pvg32", "valve"]
    facts = commercial.facts(rows)
    assert [f.id for f in facts] == ["K1", "K2"] and facts[0].role == "commercial"


def _data():
    rows = []
    for pid, brand in (("pvg32", "Danfoss"), ("m4-12", "Bosch Rexroth")):
        rows.append({"product_id": pid, "brand": brand, "product_name": pid, "family": "valve",
                     "attribute": "max_pressure_continuous", "attribute_original": "", "value": "350",
                     "unit": "bar", "norm_value": 350.0, "norm_unit": "bar", "source_file": f"{pid}.pdf",
                     "page": 3, "csv_file": "x.csv", "csv_line": 1, "quote": "350 bar", "status": "ok",
                     "condition": "", "note": "", "based_on": [], "role": ""})
    return pd.DataFrame(rows)


def test_interpret_sends_approved_notes_and_ai_may_cite_them(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    captured = {}

    def fake(system, prompt, schema=None):
        captured["prompt"] = prompt
        import json
        return json.dumps({"comparable": True, "not_comparable_reason": None,
                           "best_match": {"product_id": "m4-12", "why": "Same pressure class.",
                                          "fact_ids": ["F1", "F2"]},
                           "alternatives": [], "points": [],
                           "application_fit": {"text": "Go-to product in the platform.", "fact_ids": ["K1"]},
                           "talking_points": [{"text": "Positioning: the go-to product in the PVG platform.",
                                               "fact_ids": ["K1"]}],
                           "tradeoffs": [], "verify": [], "overall_confidence": "medium"})

    r = interpret("pvg32", ["m4-12"], ask=fake, pool="competitors", data=_data(), use_cache=False)
    assert "APPROVED COMMERCIAL NOTES" in captured["prompt"] and "K1 | pvg32 | positioning" in captured["prompt"]
    # the PVG32 note is relevant; the family note about PVG120 is not (no product of this comparison, no customer
    # match), so it is kept aside instead of being shown in every comparison
    assert r.status == "ok" and r.meta["commercial_notes"] == 1
    assert [n["id"] for n in r.meta["commercial_other"]] == ["COM-002"]
    assert not r.meta.get("problems")                                   # citing K1 passes the guardrails


def test_website_shows_commercial_block_with_source(monkeypatch, tmp_path):
    _seed(monkeypatch, tmp_path)
    from comparison_engine.website import to_website
    r = interpret("pvg32", ["m4-12"], use_ai=False, data=_data(), use_cache=False)
    w = to_website(r)
    block = w["commercial"]
    assert len(block) == 1 and block[0]["kind"] == "positioning"
    assert [n["id"] for n in w["commercialOther"]] == ["COM-002"]
    cited = [s for s in w["sources"] if s["kind"] == "commercial"]
    assert cited and cited[0]["title"].startswith("Sales guideline:") and cited[0]["url"] is None


def test_backend_endpoints_enforce_roles(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import backend
    monkeypatch.setenv("COMMERCIAL_FILE", str(tmp_path / "commercial.csv"))
    client = TestClient(backend.app)
    res = client.post("/api/commercial", json={"productId": "pvg32", "kind": "positioning", "text": "X."},
                      headers={"X-User": "junior"}).json()
    assert res["note"]["status"] == "pending" and "review" in res["issues"][0]["text"]
    denied = client.post("/api/commercial/decide", json={"id": res["note"]["id"], "action": "approve"},
                         headers={"X-User": "junior"})
    assert denied.status_code == 403
    ok = client.post("/api/commercial/decide", json={"id": res["note"]["id"], "action": "approve"},
                     headers={"X-User": "senior"}).json()
    assert ok["note"]["status"] == "approved"
    both = client.get("/api/commercial", params={"product": "pvg32"}).json()
    assert [r["id"] for r in both["approved"]] == [res["note"]["id"]] and both["pending"] == []


def test_family_notes_match_singular_and_plural(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMERCIAL_FILE", str(tmp_path / "commercial.csv"))
    commercial.add("valves", "positioning", "Catalog-family note.", EDITOR)       # catalog says 'valves'
    assert commercial.for_products(["pvg32"], family="valve")                     # engine says 'valve'
    assert commercial.for_products(["pvg32"], family="valves")


def test_grounding_meter_counts_statements_citations_numbers(monkeypatch, tmp_path):
    monkeypatch.setenv("COMMERCIAL_FILE", str(tmp_path / "commercial.csv"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    import json
    from comparison_engine.website import to_website

    def fake(system, prompt, schema=None):
        return json.dumps({"comparable": True, "not_comparable_reason": None,
                           "best_match": {"product_id": "m4-12", "why": "Same 350 bar pressure class.",
                                          "fact_ids": ["F1", "F2"]},
                           "alternatives": [], "points": [],
                           "application_fit": {"text": "Fits the pressure need.", "fact_ids": ["F1"]},
                           "talking_points": [], "tradeoffs": [], "verify": [],
                           "overall_confidence": "high"})

    r = interpret("pvg32", ["m4-12"], ask=fake, pool="competitors", data=_data(), use_cache=False)
    g = r.meta["grounding"]
    assert g == {"statements": 2, "citations": 3, "numbers": 1, "problems": 0, "attempts": 1}
    assert to_website(r)["grounding"] == g
