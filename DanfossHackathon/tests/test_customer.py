"""Customer feedback files: text -> CSV (file not kept), relevant passages as citable facts C1.., checked by code."""
import io
import json
from pathlib import Path

import pandas as pd
import pytest

from comparison_engine import interpret
from comparison_engine.customer import customer_facts, file_to_rows, list_customers, save_customer_file
from test_interpret import COMPX, GOOD, P75, fake

APP = Path(__file__).resolve().parent.parent
KPES_PDF = next((APP / "data" / "files").glob("*/*KPES*.pdf"), None)


def excel_bytes() -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as xl:
        pd.DataFrame({"Topic": ["Flow", "Noise", "Delivery"],
                      "Requirement": ["Needs at least 120 l/min at the loader valve",
                                      "Current valve is too loud in the cab",
                                      "Lead time below 8 weeks"]}).to_excel(xl, sheet_name="Needs", index=False)
    return buf.getvalue()


@pytest.fixture
def customers(tmp_path, monkeypatch):
    monkeypatch.setenv("CUSTOMER_DIR", str(tmp_path / "customers"))
    save_customer_file("AGCO", "needs.xlsx", excel_bytes())
    return tmp_path / "customers"


def test_excel_rows_keep_sheet_and_row():
    rows = file_to_rows("needs.xlsx", excel_bytes())
    assert rows[0] == {"source_file": "needs.xlsx [Needs]", "page": 2,
                       "text": "Topic: Flow; Requirement: Needs at least 120 l/min at the loader valve"}


@pytest.mark.skipif(KPES_PDF is None, reason="uploaded KPES leaflet not available")
def test_pdf_pages():
    rows = file_to_rows("leaflet.pdf", KPES_PDF.read_bytes())
    assert rows[0]["page"] == 1 and "KPES" in rows[0]["text"]


def test_unsupported_type_is_rejected():
    with pytest.raises(ValueError):
        file_to_rows("photo.png", b"x")


def test_only_csv_is_stored_and_never_committed(customers):
    files = sorted(p.name for p in customers.rglob("*") if p.is_file())
    assert files == [".gitignore", "needs.csv"]                   # no .xlsx copy
    assert (customers / ".gitignore").read_text() == "*\n"
    assert list_customers() == {"AGCO": ["needs"]}


def test_relevant_passages_become_facts_c1_c2(customers):
    cf = customer_facts("agco", ["flow"])                          # name matching ignores case
    assert [c.id for c in cf] == ["C1", "C2", "C3"] and cf[0].role == "customer"
    assert "120 l/min" in cf[0].value and cf[0].source_file == "needs.xlsx [Needs]" and cf[0].page == 2
    assert customer_facts("Unknown customer") == []


def answer_citing(text, ids):
    a = json.loads(json.dumps(GOOD))
    a["talking_points"] = [{"text": text, "fact_ids": ids}]
    return a


def test_ai_may_cite_customer_numbers(customers):
    cf = customer_facts("AGCO", ["flow"])
    r = interpret(COMPX, None, ask=fake(answer_citing("AGCO needs at least 120 l/min at the loader valve.", ["C1"])),
                  customer_facts=cf)
    assert r.status == "ok", r.problems
    assert r.meta["customer_passages"] == 3


def test_invented_customer_number_is_caught(customers):
    cf = customer_facts("AGCO", ["flow"])
    bad = answer_citing("AGCO needs at least 150 l/min.", ["C1"])
    r = interpret(COMPX, None, ask=fake(bad, bad), customer_facts=cf)
    assert any("number 150" in p for p in r.problems)


def test_customer_passage_is_not_product_data(customers):
    cf = customer_facts("AGCO", ["flow"])
    a = json.loads(json.dumps(GOOD))
    a["points"][0]["target_facts"] = ["C1"]
    r = interpret(COMPX, None, ask=fake(a, a), customer_facts=cf)
    assert any("C1 is not a fact of the target" in p for p in r.problems)


def test_prompt_lists_customer_passages(customers):
    ask = fake(GOOD)
    interpret(P75, None, ask=ask, pool="competitors", customer_facts=customer_facts("AGCO", ["flow"]),
              use_cache=False)
    assert 'C1 | needs.xlsx [Needs] p.2 | "Topic: Flow; Requirement: Needs at least 120 l/min' in ask.calls[0]["prompt"]
