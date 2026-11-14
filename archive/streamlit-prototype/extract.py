"""
Document in the library -> AI extraction -> verified values in the library (owner: P2).

Add a datasheet and extract one or more products from it (in the Terminal, .venv active):

    python extract.py data/raw/danfoss_s45.pdf --brand Danfoss --family pump \
        --product "S45 J 45" --product "S45 J 75" --url "https://powersource.danfoss.com/..."

Only store the file, no AI (works without an API key - good for P1 tonight):

    python extract.py data/raw/rexroth_a10vo.pdf --brand "Bosch Rexroth" --family pump --no-ai

The PDF is copied into the library (static/docs/), so data/raw/ is just a drop zone.
"""
import argparse
import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

import db
from llm import extract_attributes
from schema import attributes

# words that show up on spec pages - used to pick the most relevant pages for the AI
KEYWORDS = ["pressure", "displacement", "speed", "flow", "bar", "psi", "cm3", "cm³", "l/min",
            "rpm", "min-1", "weight", "mass", "technical data", "specification", "model code"]


def pick_pages(pages: dict[int, str], max_pages: int = 12) -> dict[int, str]:
    """Send only the most spec-heavy pages to the AI (cheaper and more accurate)."""
    scored = sorted(pages.items(), key=lambda kv: -sum(kv[1].lower().count(k) for k in KEYWORDS))
    best = sorted(p for p, _ in scored[:max_pages])
    return {p: pages[p] for p in best}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def verify_quote(quote, page_text) -> bool:
    """Is the AI's quote really on that page? (protects us against hallucinations)"""
    if not quote or not page_text:
        return False
    return fuzz.partial_ratio(_norm(quote), _norm(page_text)) >= 90


def extract_product(doc_id: int, brand: str, product: str, family: str) -> pd.DataFrame:
    """Run the AI over a stored document for ONE product and save the verified result."""
    pages = db.get_pages(doc_id)
    results = extract_attributes(pick_pages(pages), family, product)
    found = {r.get("attribute"): r for r in results}

    rows = []
    for attr in attributes(family):  # loop over OUR schema, so nothing is silently dropped
        r = found.get(attr, {})
        value, page, quote = r.get("value"), r.get("page"), r.get("quote")
        if value in (None, "", "null"):
            status = "missing"
        elif verify_quote(quote, pages.get(int(page), "") if page else ""):
            status = "ok"
        else:
            status = "unverified"  # value given, but quote not found on that page
        rows.append({"attribute": attr, "value": value, "unit": r.get("unit"),
                     "page": page, "quote": quote, "status": status})
    df = pd.DataFrame(rows)
    db.save_attributes(df, brand, product, family, doc_id)
    return df


def add_and_extract(path: str, brand: str, family: str, products: list[str], url: str = "",
                    doc_type: str = "datasheet", run_ai: bool = True, force: bool = False):
    doc_id, is_new = db.add_document(Path(path).read_bytes(), Path(path).name, brand, family, doc_type, url)
    print(f"{'Added' if is_new else 'Already in library'}: {Path(path).name} (doc_id {doc_id})")
    if not run_ai:
        return
    for product in products:
        if not force and db.already_extracted(f"{brand}|{product}", doc_id):
            print(f"  {product}: already extracted from this document (use --force to redo)")
            continue
        df = extract_product(doc_id, brand, product, family)
        print(f"\n  {product}:")
        print(df[["attribute", "value", "unit", "page", "status"]].to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--brand", required=True)
    ap.add_argument("--family", required=True, choices=["pump", "valve"])
    ap.add_argument("--product", action="append", default=[], help="repeat for several sizes")
    ap.add_argument("--url", default="")
    ap.add_argument("--type", default="datasheet", help="datasheet / brochure / webshop / catalogue")
    ap.add_argument("--no-ai", action="store_true", help="only store the file in the library")
    ap.add_argument("--force", action="store_true", help="re-run the AI even if already extracted")
    a = ap.parse_args()
    if not a.no_ai and not a.product:
        ap.error("give at least one --product (or use --no-ai)")
    add_and_extract(a.file, a.brand, a.family, a.product, a.url, a.type, not a.no_ai, a.force)
