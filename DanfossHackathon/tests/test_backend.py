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


def test_customer_feedback_upload_and_use(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_DIR", str(tmp_path / "customers"))
    files = {"files": ("needs.txt", b"AGCO needs at least 90 l/min per section.\nCab noise is a problem.", "text/plain")}
    r = client.post("/api/datasets", data={"source": "customer", "customerName": "AGCO"}, files=files).json()
    assert r["uploaded"] == ["needs.txt"] and "original file not stored" in r["issues"][0]["text"]
    assert client.get("/api/catalog").json()["customers"] == [{"name": "AGCO", "files": ["needs"]}]
    calls = []

    def fake(system, prompt, schema=None):
        calls.append(prompt)
        raise LLMUnavailable("no key")
    monkeypatch.setattr(backend, "ENGINE_ASK", fake)
    resp = client.post("/api/compare", json=brief(competitorProductIds=["rexroth-sb23"])).json()
    assert "C1 | needs.txt p.1" in calls[0]
    assert any(f["level"] == "ok" and "Customer feedback considered" in f["text"] for f in resp["flags"])


def test_customer_upload_needs_a_name(client):
    r = client.post("/api/datasets", data={"source": "customer"}, files={"files": ("a.txt", b"x", "text/plain")}).json()
    assert r["uploaded"] == [] and r["issues"][0]["level"] == "error"


def add_demo_pumps(monkeypatch):
    import os
    repo = Path(os.environ["REPO_DIR"])
    for name in ("danfoss_DEMO_pumps.csv", "competitorx_DEMO_pump71.csv"):
        shutil.copy(APP / "examples" / "repository_demo" / name, repo / name)


def test_products_list_for_the_system_builder(client, monkeypatch):
    add_demo_pumps(monkeypatch)
    prods = client.get("/api/products").json()["products"]
    by_id = {p["id"]: p for p in prods}
    assert by_id["pvg32"]["family"] == "valve" and by_id["pvg32"]["side"] == "danfoss"
    assert by_id["rexroth-sb23"]["side"] == "competitor"
    assert by_id["Danfoss|DEMO pump 75"]["family"] == "pump" and by_id["Danfoss|DEMO pump 75"]["demo"]


def test_compare_system_pump_and_valve(client, monkeypatch):
    add_demo_pumps(monkeypatch)
    r = client.post("/api/compare-system", json={
        "start": "danfoss", "components": {"pump": "Danfoss|DEMO pump 75", "valve": "pvg32"},
        "against": {"pump": None, "valve": "rexroth-sb23"}, "buyer": {"company": "AGCO"}}).json()
    assert r["anchor"]["name"] == "Danfoss system" and "PVG 32 (valve)" in r["anchor"]["manufacturer"]
    assert r["matches"][0]["manufacturer"] == "DEMO pump 71 (pump) + SB 23 (valve)"
    labels = [a["label"] for a in r["attributes"] if a["core"]]
    assert "System · Max flow" in labels and any(l.startswith("Pump · ") for l in labels)
    assert any(f["level"] == "unverified" and "pump: chosen automatically" in f["text"] for f in r["flags"])


def test_compare_system_needs_two_components_with_data(client):
    r = client.post("/api/compare-system", json={"components": {"valve": "pvg32"}}).json()
    assert r["flags"][0]["level"] == "missing" and "at least two" in r["flags"][0]["text"]
    r = client.post("/api/compare-system", json={"components": {"valve": "pvg32", "pump": "nope"}}).json()
    assert "No extracted facts yet for: nope" in r["flags"][0]["text"]


class FakeConverter:
    """Stands in for the teammates' pdf_to_csv_test.py: no AI, fixed rows."""
    calls = []

    @staticmethod
    def parse_pages(spec):
        return {1}

    @classmethod
    def process_pdf(cls, path, page_filter, client, provider, company, dry_run):
        cls.calls.append({"name": Path(path).name, "pages": page_filter, "company": company})
        print("  page 1: asking the AI (waits up to 90s)...")
        name = Path(path).name
        rows = [{"manufacturer": company, "product": "SB 23", "attribute": "max_pressure_port_P", "value": "300",
                 "unit": "bar", "page": 1, "quote": "300 bar", "quote_verified": True, "uncertain": False,
                 "fact_type": "spec", "source_file": name},
                {"manufacturer": company, "product": "SB 23", "attribute": "ordering_code_spool", "value": "A;B",
                 "fact_type": "ordering_code", "page": 1, "source_file": name}]
        return rows, [{"source_file": name, "page": 1, "text": "Max pressure 300 bar"}]


def wait_for(client, job_id, seconds=5):
    import time
    for _ in range(int(seconds * 20)):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    return job


def test_upload_then_extract_facts(client, tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(backend, "CONVERTER", FakeConverter)
    monkeypatch.setattr(backend, "EXTRACT_CLIENT", object())
    monkeypatch.setenv("PAGES_DIR", str(tmp_path / "pages"))
    up = client.post("/api/datasets", data={"source": "competitor", "competitorId": "rexroth", "productName": "SB 23",
                                            "categoryId": "proportional"},
                     files={"files": ("SB23_datasheet.pdf", b"%PDF-1.4 demo", "application/pdf")}).json()
    ds = up["datasets"][0]
    assert ds["ownerId"] == "rexroth-sb23" and ds["name"] == "SB23_datasheet.pdf"
    start = client.post("/api/extract", json={**ds, "pages": "1"}).json()
    job = wait_for(client, start["jobId"])
    assert job["status"] == "done", job
    assert "1 facts saved" in job["progress"] and "1 ordering-code rows skipped" in job["progress"]
    assert FakeConverter.calls[-1] == {"name": "SB23_datasheet.pdf", "pages": {1}, "company": "Bosch Rexroth"}
    csv = Path(os.environ["REPO_DIR"]) / f"rexroth-sb23__{ds['fileId']}.csv"
    assert csv.exists() and "max_pressure_port_p" in csv.read_text()
    assert (tmp_path / "pages" / f"{ds['fileId']}.csv").exists()
    cat = client.get("/api/catalog").json()
    prod = next(p for p in cat["competitorProducts"] if p["id"] == "rexroth-sb23")
    assert next(d for d in prod["datasets"] if d["fileId"] == ds["fileId"])["facts"]["facts"] == 1


def test_extract_refuses_without_key_or_non_pdf(client, monkeypatch):
    up = client.post("/api/datasets", data={"source": "danfoss", "nodeId": "pvg48"},
                     files={"files": ("notes.csv", b"a,b\n1,2\n", "text/csv")}).json()
    r = client.post("/api/extract", json=up["datasets"][0]).json()
    assert r["jobId"] is None and "Only PDF" in r["issues"][0]["text"]
    up = client.post("/api/datasets", data={"source": "danfoss", "nodeId": "pvg48"},
                     files={"files": ("pvg48.pdf", b"%PDF-1.4 demo", "application/pdf")}).json()
    r = client.post("/api/extract", json=up["datasets"][0]).json()               # conftest removed the key
    assert r["jobId"] is None and "ANTHROPIC_API_KEY" in r["issues"][0]["text"]


def test_web_discover_and_import(client, tmp_path, monkeypatch):
    from comparison_engine.documents import link
    monkeypatch.setattr(backend, "web_discover", lambda url, topic="pumps": {
        "documents": [{"url": "https://example.com/sb23.pdf", "name": "sb23.pdf", "product": "SB 23",
                       "label": "PDF", "kind": "auto"}], "pageUrl": url, "pagesVisited": 2, "limited": False,
        "cached": False, "warnings": []})
    monkeypatch.setattr(backend, "web_download", lambda url: (b"%PDF-1.4 demo", "sb23.pdf",
                                                              "https://cdn.example.com/docs/sb23.pdf", False))
    found = client.post("/api/web/discover", json={"url": "https://example.com/valves", "topic": "valves"}).json()
    assert found["documents"][0]["url"] == "https://example.com/sb23.pdf" and found["issues"] == []
    r = client.post("/api/web/import", json={"url": "https://example.com/sb23.pdf", "source": "competitor",
                                             "competitorId": "rexroth", "productName": "SB 23",
                                             "categoryId": "proportional", "title": "SB 23 datasheet"}).json()
    assert r["uploaded"] == ["sb23.pdf"] and r["datasets"][0]["ownerId"] == "rexroth-sb23"
    assert link("sb23.pdf", 4)["url"] == "https://cdn.example.com/docs/sb23.pdf#page=4"      # footnotes open it online
    assert link("sb23.pdf", 4)["title"] == "SB 23 datasheet"


def test_web_errors_are_shown_not_raised(client, monkeypatch):
    def boom(url, topic="pumps"):
        raise ValueError("Local and private network addresses are blocked.")
    monkeypatch.setattr(backend, "web_discover", boom)
    r = client.post("/api/web/discover", json={"url": "http://localhost/x"}).json()
    assert r["documents"] == [] and "blocked" in r["issues"][0]["text"]


class FailingConverter(FakeConverter):
    """Every AI call fails (e.g. a new key without credit): the converter prints it and returns no rows."""
    @classmethod
    def process_pdf(cls, path, page_filter, client, provider, company, dry_run):
        print("  3 pages in the file; 2 to send to the AI, 1 skipped (blank/cover/no data)")
        print("  page 1: AI call or JSON parsing failed: Error code: 400 - credit balance is too low")
        print("  page 2: AI call or JSON parsing failed: Error code: 400 - credit balance is too low")
        return [], [{"source_file": Path(path).name, "page": 1, "text": "x"}]


def test_extraction_with_zero_facts_says_why_and_stays_in_the_list(client, tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "CONVERTER", FailingConverter)
    monkeypatch.setattr(backend, "EXTRACT_CLIENT", object())
    monkeypatch.setenv("PAGES_DIR", str(tmp_path / "pages"))
    up = client.post("/api/datasets", data={"source": "competitor", "competitorId": "rexroth", "productName": "M4-12",
                                            "categoryId": "proportional"},
                     files={"files": ("m4-12.pdf", b"%PDF-1.4 demo", "application/pdf")}).json()
    ds = up["datasets"][0]
    job = wait_for(client, client.post("/api/extract", json=ds).json()["jobId"])
    assert job["status"] == "failed"                                   # was: "done" with "0 facts saved"
    assert "2 page(s) failed" in job["progress"] and "credit balance" in job["progress"]
    prod = next(p for p in client.get("/api/catalog").json()["competitorProducts"] if p["id"] == "rexroth-m4-12")
    assert not next(d for d in prod["datasets"] if d["fileId"] == ds["fileId"]).get("facts")   # can be retried


def test_dani_knows_its_name_and_stays_grounded(client, monkeypatch):
    r = client.post("/api/ask", json={"question": "Who are you?"}).json()
    assert "Dani" in r["answer"] and "cite" in r["answer"]
    r = client.post("/api/ask", json={"question": "Hey Dani!"}).json()
    assert "Dani" in r["answer"]
    # the name is stripped before keyword matching and the context hint still works
    r = client.post("/api/ask", json={"question": "Dani, what is the max flow?"}).json()
    assert "Pick a product" in r["answer"]


def test_ask_prompt_carries_the_dani_persona(client, monkeypatch):
    sent = {}

    class FakeMsgs:
        def create(self, **kw):
            sent.update(kw)
            from types import SimpleNamespace as NS
            return NS(content=[NS(type="text", text="42 l/min (leaflet, p. 2)")],
                      usage=NS(input_tokens=10, output_tokens=5))

    monkeypatch.setattr(backend, "claude", lambda: type("C", (), {"messages": FakeMsgs()})())
    up = client.post("/api/datasets", data={"source": "danfoss", "nodeId": "pvg32"},
                     files={"files": ("x.pdf", b"%PDF-1.4", "application/pdf")}).json()
    r = client.post("/api/ask", json={"question": "Dani, what is the max flow?",
                                      "context": {"productId": "pvg32"}}).json()
    assert "42 l/min" in r["answer"]
    assert "You are Dani" in sent["system"] and "never fill the gap from memory" in sent["system"]
    assert "dani" not in sent["messages"][0]["content"].split("Question:")[1].lower()
