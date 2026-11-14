"""
BRIDGE - the teammates' PDF converter (pdf_to_csv_test.py, repo root) -> the app's facts CSVs.

The converter is NOT changed. This module calls it and translates its rows:

    converter column                          app column
    manufacturer                        ->    brand
    product (or product_family)         ->    product_name, and product_id = the WEBSITE CATALOG ID when the
                                              product is the one the file was uploaded for (else "Brand|Product")
    attribute + variant                 ->    attribute  (rated_flow + variant "S" -> rated_flow_s, so sizes and
                                              versions get their own rows instead of fake conflicts)
    condition                           ->    condition  (values under different conditions are not a conflict)
    quote_verified and not uncertain    ->    status "ok"; otherwise "unverified"; empty value -> "missing"
    fact_type "ordering_code"           ->    skipped (long code tables are not comparison facts)
    unit "degrees C"                    ->    "°C"

    rows, report = to_facts(converter_rows, owner_id="pvg32", owner_name="PVG 32", family="valve", brand="Danfoss")
    extract_pdf(path, owner_id, owner_name, family, brand, pages="1-20")   # runs the converter (AI) + saves

Output: data/repository/<owner_id>__<file_id>.csv (facts) and data/pages/<file_id>.csv (page text, so the
original PDF does not have to be kept).
"""
import importlib.util
import os
import re
from pathlib import Path

import pandas as pd

from . import config
from .repository import validate_csv

FACT_COLUMNS = ["product_id", "brand", "product_name", "family", "attribute", "value", "unit", "source_file",
                "page", "quote", "status", "condition", "fact_type"]
SKIP_TYPES = {"ordering_code"}
UNIT_FIX = {"degrees c": "°C", "deg c": "°C", "degc": "°C", "° c": "°C", "c°": "°C"}


def converter_path() -> Path:
    return Path(os.getenv("CONVERTER_PATH", config.PROJECT_ROOT.parent / "pdf_to_csv_test.py"))


def pages_dir() -> Path:
    return Path(os.getenv("PAGES_DIR", config.PROJECT_ROOT / "data" / "pages"))


def load_converter():
    """Import the teammates' script as a module (its main() does not run on import)."""
    path = converter_path()
    if not path.exists():
        raise FileNotFoundError(f"converter not found: {path}")
    spec = importlib.util.spec_from_file_location("pdf_to_csv_converter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _squash(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _slug(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def _true(v) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and v != v) or str(v).strip() in ("", "nan", "None")


def match_products(products: list[str], owner_name: str) -> set[str]:
    """Which product names in the file are the product the user uploaded it for.
    One product in the file -> that one. Several -> those whose name contains the owner's name (or vice versa)."""
    names = [p for p in dict.fromkeys(products) if _squash(p)]
    if len(names) == 1:
        return set(names)
    owner = _squash(owner_name)
    return {p for p in names if owner and (owner in _squash(p) or _squash(p) in owner)}


def to_facts(rows: list[dict], owner_id: str, owner_name: str, family: str, brand: str) -> tuple[pd.DataFrame, dict]:
    """Converter rows -> app facts rows + a short report (what was kept, skipped, matched)."""
    report = {"rows_in": len(rows), "skipped_ordering_code": 0, "facts": 0, "ok": 0, "unverified": 0,
              "missing": 0, "owner_products": [], "other_products": []}
    product_of = lambda r: str(r.get("product") or r.get("product_family") or "").strip()  # noqa: E731
    mine = match_products([product_of(r) for r in rows], owner_name)
    out = []
    for r in rows:
        if str(r.get("fact_type", "")).strip().lower() in SKIP_TYPES:
            report["skipped_ordering_code"] += 1
            continue
        product = product_of(r) or owner_name
        attribute = _slug(r.get("attribute"))
        if not attribute:
            continue
        if not _empty(r.get("variant")):
            attribute += "_" + _slug(r["variant"])
        value = "" if _empty(r.get("value")) else str(r["value"]).strip()
        status = ("missing" if not value else
                  "ok" if _true(r.get("quote_verified")) and not _true(r.get("uncertain")) else "unverified")
        unit = "" if _empty(r.get("unit")) else str(r["unit"]).strip()
        unit = UNIT_FIX.get(unit.lower(), unit)
        maker = str(r.get("manufacturer") or "").strip()
        out.append({
            "product_id": owner_id if product in mine else f"{maker or brand}|{product}",
            "brand": brand if product in mine else (maker or brand),
            "product_name": owner_name if product in mine else product,
            "family": family, "attribute": attribute, "value": value, "unit": unit,
            "source_file": r.get("source_file"), "page": r.get("page") if value else "",
            "quote": "" if _empty(r.get("quote")) else str(r["quote"]).strip(),
            "status": status, "condition": "" if _empty(r.get("condition")) else str(r["condition"]).strip(),
            "fact_type": str(r.get("fact_type") or ""),
        })
        report[status] += 1
    df = pd.DataFrame(out, columns=FACT_COLUMNS)
    report["facts"] = len(df)
    report["owner_products"] = sorted(mine)
    report["other_products"] = sorted({p for p in df["product_name"] if p != owner_name})
    return df, report


def save_facts(df: pd.DataFrame, owner_id: str, file_id: str, repo_dir: Path | None = None) -> tuple[Path, list[str]]:
    """Write data/repository/<owner_id>__<file_id>.csv. Returns (path, row warnings from validate_csv)."""
    folder = Path(repo_dir) if repo_dir else config.repo_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_slug(owner_id) or 'product'}__{file_id}.csv"
    df.to_csv(path, index=False)
    return path, validate_csv(df)


def save_pages(pages: list[dict], file_id: str) -> Path:
    """Page text as CSV (source_file, page, text): lets us check quotes later without keeping the PDF."""
    folder = pages_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}.csv"
    pd.DataFrame([{k: p.get(k) for k in ("source_file", "page", "text")} for p in pages]).to_csv(path, index=False)
    return path


def extract_pdf(pdf_path: Path, owner_id: str, owner_name: str, family: str, brand: str, file_id: str,
                pages: str = "", client=None, converter=None, log=print) -> dict:
    """Run the converter (one AI call per selected page) and save facts + page text. Returns the report."""
    conv = converter or load_converter()
    page_filter = conv.parse_pages(pages) if pages.strip() else None
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=90)
    rows, page_texts = conv.process_pdf(str(pdf_path), page_filter, client, "claude", brand, False)
    df, report = to_facts(rows, owner_id, owner_name, family, brand)
    facts_path, warnings = save_facts(df, owner_id, file_id)
    save_pages(page_texts, file_id)
    report.update(csv=facts_path.name, warnings=warnings[:10], pages_sent=len(page_filter) if page_filter else len(page_texts))
    log(f"saved {report['facts']} facts ({report['ok']} ok, {report['unverified']} unverified) to {facts_path.name}")
    return report
