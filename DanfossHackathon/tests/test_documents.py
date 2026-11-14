"""documents.csv: online links for sources (filled by the datasheet crawler)."""
import pandas as pd

from comparison_engine import interpret
from comparison_engine.documents import link, validate_documents
from comparison_engine.website import to_website
from test_interpret import COMPX

DOCS = """source_file,url,title,revision,date,found_by,checked_at
DEMO_compx.pdf,https://example.com/docs/compx.pdf,CompetitorX pump datasheet,A,2024-01,crawler,2026-11-14
DEMO_webshop.txt,https://example.com/shop/pump71,Webshop page,,,crawler,2026-11-14
DEMO_danfoss.pdf,,Danfoss DEMO datasheet,,,manual,
"""


def write(tmp_path, text=DOCS):
    p = tmp_path / "documents.csv"
    p.write_text(text, encoding="utf-8")
    return p


def test_pdf_link_opens_the_page(tmp_path):
    p = write(tmp_path)
    assert link("DEMO_compx.pdf", 4, p)["url"] == "https://example.com/docs/compx.pdf#page=4"
    assert link("DEMO_webshop.txt", 1, p)["url"] == "https://example.com/shop/pump71"     # not a PDF: no #page
    assert link("DEMO_danfoss.pdf", 3, p) is None                                         # known, no url yet
    assert link("unknown.pdf", 1, p) is None


def test_validate_documents():
    bad = pd.DataFrame([{"source_file": "a.pdf", "url": "ftp://x"}, {"source_file": "a.pdf", "url": ""}])
    problems = " | ".join(validate_documents(bad))
    assert "http(s)" in problems and "listed twice" in problems
    assert validate_documents(pd.DataFrame([{"url": "https://x"}])) == ["missing column: source_file"]


def test_sources_in_the_result_use_the_online_link(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCUMENTS_FILE", str(write(tmp_path)))
    w = to_website(interpret(COMPX, None, use_ai=False), doc_url=lambda f, p: link(f, p))
    by_page = {(s["file"], s["page"]): s for s in w["sources"]}           # one source per document page
    assert by_page[("DEMO_compx.pdf", "4")]["url"] == "https://example.com/docs/compx.pdf#page=4"
    assert by_page[("DEMO_compx.pdf", "12")]["url"] == "https://example.com/docs/compx.pdf#page=12"
    assert by_page[("DEMO_compx.pdf", "4")]["title"] == "CompetitorX pump datasheet"
    assert all(s["url"] is None for s in w["sources"] if s["file"] == "DEMO_danfoss.pdf")


def test_broken_documents_file_is_ignored(tmp_path):
    assert link("DEMO_compx.pdf", 1, write(tmp_path, "nonsense\n\x00")) is None
