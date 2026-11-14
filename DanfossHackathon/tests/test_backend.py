"""
Website API tests (FastAPI test client). Everything runs on temporary copies:
the real catalog, uploads and facts CSVs are never touched, and the AI is always fake.
"""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend
from comparison_engine.llm import LLMUnavailable

APP = Path(__file__).resolve().parent.parent
CSV = """product_id,brand,product_name,family,attribute,value,unit,source_file,page,quote,status
pvg32,Danfoss,PVG 32,valves,max_flow_per_section,100,l/min,DEMO_pvg32.pdf,12,DEMO 100 l/min with compensator,ok
pvg32,Danfoss,PVG 32,valves,rated_pressure,350,bar,DEMO_pvg32.pdf,12,DEMO 350 bar continuous,ok
pvg32,Danfoss,PVG 32,valves,max_work_sections,12,,DEMO_pvg32.pdf,7,DEMO up to 12 sections,ok
rexroth-sb23,Bosch Rexroth,SB 23,valves,max_flow_per_section,26,gpm,DEMO_sb23.pdf,3,DEMO 26 gpm,ok
rexroth-sb23,Bosch Rexroth,SB 23,valves,rated_pressure,4350,psi,DEMO_sb23.pdf,3,DEMO 4350 psi,ok
rexroth-sb23,Bosch Rexroth,SB 23,valves,max_work_sections,,,DEMO_sb23.pdf,,,missing
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "files" / "pvg32").mkdir(parents=True)
    (data / "files" / "pvg32" / "abc123_DEMO_pvg32.pdf").write_bytes(b"%PDF-demo")
    (data / "extracted").mkdir()
    cat = json.loads((APP / "data" / "catalog.json").read_text(encoding="utf-8"))
    cat["competitorProducts"].append({"id": "rexroth-sb23", "competitorId": "rexroth", "name": "SB 23",
                                      "categoryId": "proportional", "equivalentOf": ["pvg32"],
                                      "datasets": [{"name": "DEMO_sb23.pdf", "fileId": "x"}], "status": "active"})
    (data / "catalog.json").write_text(json.dumps(cat), encoding="utf-8")
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / "demo_valves.csv").write_text(CSV, encoding="utf-8")

    monkeypatch.setattr(backend, "DATA_DIR", data)
    monkeypatch.setattr(backend, "FILES_DIR", data / "files")
    monkeypatch.setattr(backend, "EXTRACT_DIR", data / "extracted")
    monkeypatch.setattr(backend, "CATALOG_FILE", data / "catalog.json")
    monkeypatch.setenv("REPO_DIR", str(repo))
    monkeypatch.setattr(backend, "ENGINE_ASK", no_ai)
    return TestClient(backend.app)


def no_ai(system, prompt, schema=None):
    raise LLMUnavailable("no key in tests")


def brief(**extra):
    return {"path": ["valves", "proportional", "pvg32"], "productId": "pvg32", "competitors": [],
            "factors": {"pressure": "must"}, "customFactors": [], "competitorProductIds": [],
            "buyer": {"company": "AGCO", "application": "Tractor"}, "chatNotes": [], **extra}


def cell(resp, attr, key):
    return next(a for a in resp["attributes"] if a["id"] == attr)["values"][key]


def test_catalog_still_works(client):
    assert client.get("/api/catalog").status_code == 200


def test_compare_chosen_competitor(client):
    r = client.post("/api/compare", json=brief(competitorProductIds=["rexroth-sb23", "kawasaki-kmp"])).json()
    assert r["anchor"]["key"] == "pvg32" and r["anchor"]["name"] == "PVG 32"
    keys = [m["key"] for m in r["matches"]]
    assert keys == ["rexroth-sb23", "kawasaki-kmp"]
    sb23 = r["matches"][0]
    assert sb23["manufacturer"] == "Bosch Rexroth" and sb23["knownEquivalent"]
    assert cell(r, "max_pressure_continuous", "rexroth-sb23")["status"] == "converted"   # psi -> bar, by code
    assert cell(r, "max_work_sections", "rexroth-sb23")["status"] == "missing"
    assert cell(r, "max_pressure_continuous", "kawasaki-kmp")["status"] == "pending"     # uploaded, no CSV yet
    assert r["confidence"]["level"] == "low"                                        # no AI in this test
    assert r["sources"][0]["url"] == "/files/pvg32/abc123_DEMO_pvg32.pdf#page=12"


def test_compare_auto_finds_competitors_in_the_facts(client):
    r = client.post("/api/compare", json=brief()).json()
    assert "rexroth-sb23" in [m["key"] for m in r["matches"]]
    assert any(f["level"] == "unverified" and "automatically" in f["text"] for f in r["flags"])


def test_compare_product_without_facts_says_so(client):
    r = client.post("/api/compare", json=brief(productId="pvg48")).json()
    assert r["attributes"] == [] and r["flags"][0]["level"] in ("missing", "pending")
    assert "No extracted facts for PVG 48" in r["flags"][0]["text"]


def test_compare_unknown_product(client):
    r = client.post("/api/compare", json=brief(productId="nope")).json()
    assert r["flags"][0]["level"] == "error"


def test_match_reverse_direction(client):
    r = client.post("/api/match", json={"competitorProductId": "rexroth-sb23",
                                        "buyer": {"company": "AGCO"}}).json()
    assert r["anchor"]["key"] == "rexroth-sb23"
    assert [m["key"] for m in r["matches"]] == ["pvg32"]
    assert r["engine"]["meta"]["pool"] == "danfoss"


def test_match_without_facts(client):
    r = client.post("/api/match", json={"competitorProductId": "kawasaki-kmp"}).json()
    assert r["flags"][0]["level"] == "pending" and r["attributes"] == []


def test_compare_with_checked_ai_answer(client, monkeypatch):
    answer = {"comparable": True, "not_comparable_reason": None,
              "best_match": {"product_id": "rexroth-sb23", "why": "Same job, lower rated pressure.",
                             "fact_ids": ["F2", "F5"]},   # continuous pressure of both (valve.md order)
              "alternatives": [], "points": [], "application_fit": {"text": "", "fact_ids": []},
              "talking_points": [{"text": "Up to 12 work sections in one group.", "fact_ids": ["F3"]}],
              "tradeoffs": [], "verify": [], "overall_confidence": "medium"}
    calls = []

    def fake(system, prompt, schema=None):
        calls.append(prompt)
        return json.dumps(answer)
    monkeypatch.setattr(backend, "ENGINE_ASK", fake)
    r = client.post("/api/compare", json=brief(competitorProductIds=["rexroth-sb23"])).json()
    assert r["engine"]["status"] == "ok", r["engine"]["problems"]
    assert "customer: AGCO" in calls[0] and "must-have topics: pressure" in calls[0]
    assert r["insights"]["benefits"][0]["text"] == "Up to 12 work sections in one group."
    assert r["insights"]["benefits"][0]["sources"]
    assert r["confidence"]["level"] == "medium"


def test_source_link_serves_the_stored_file(client):
    # the /files mount points at the real data/files folder; a missing file must simply 404
    assert client.get("/files/does-not-exist.pdf").status_code == 404
