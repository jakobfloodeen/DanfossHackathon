"""Quote checker + the facts CSVs shipped in data/repository/ (must always be valid and honest)."""
import json
from pathlib import Path

import pytest

from comparison_engine.quotes import verify_quote
from comparison_engine.repository import read_csv_file, validate_csv

APP = Path(__file__).resolve().parent.parent
REPO = APP / "data" / "repository"


def test_quote_found_despite_line_breaks():
    assert verify_quote("integrated pressure compensating function",
                        "control with the integrated pressure\ncompensating function.")


def test_quote_with_a_different_number_is_rejected():
    page = "Model KPES6\nMax Pressure [MPa] 17.2\nP port 70"
    assert verify_quote("Max Pressure [MPa] 17.2", page)
    assert not verify_quote("Max Pressure [MPa] 21.0", page)
    assert not verify_quote("P port 90", page)


def test_empty_quote_or_page_is_not_verified():
    assert not verify_quote("", "text") and not verify_quote("text", "")


@pytest.mark.parametrize("csv", sorted(REPO.glob("*.csv")), ids=lambda p: p.name)
def test_shipped_facts_csvs_are_valid(csv):
    assert validate_csv(read_csv_file(csv)) == []


def test_every_ok_quote_is_on_its_page():
    """For uploads we have page text of, every 'ok' row's quote must really be on that page."""
    catalog = json.loads((APP / "data" / "catalog.json").read_text(encoding="utf-8"))
    file_ids = {d["name"]: d["fileId"] for p in catalog.get("competitorProducts", []) for d in p.get("datasets", [])}
    checked = 0
    for csv in REPO.glob("*.csv"):
        for _, row in read_csv_file(csv).iterrows():
            fid = file_ids.get(row["source_file"])
            if row["status"] != "ok" or not fid or not (APP / "data" / "extracted" / f"{fid}.json").exists():
                continue
            pages = {p["page"]: p["text"] for p in json.loads(
                (APP / "data" / "extracted" / f"{fid}.json").read_text(encoding="utf-8"))}
            assert verify_quote(row["quote"], pages.get(int(row["page"]))), (csv.name, row["attribute"])
            checked += 1
    assert checked >= 9
