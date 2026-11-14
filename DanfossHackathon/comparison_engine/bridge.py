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
    extract_text(text, name, owner_id, ...)   # same for WEB PAGE text (web import saves product pages as .txt):
                                             # the text is cut into "pages" of ~4000 characters and each one goes
                                             # through the converter's own prompt, retry and quote check

Output: data/repository/<owner_id>__<file_id>.csv (facts) and data/pages/<file_id>.csv (page text, so the
original PDF does not have to be kept).
"""
import importlib.util
import os
import re
from pathlib import Path

import pandas as pd

from . import config, usage
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
    mine = {p for p in names if owner and (owner in _squash(p) or _squash(p) in owner)}
    if not mine and names:
        # none of the names in the file looks like the product it was uploaded for (e.g. the AI wrote the
        # series name): the file's most frequent product is the one it describes - otherwise ALL its facts
        # would be filed under other products and the uploaded product stays "pending" for ever
        from collections import Counter
        mine = {Counter(p for p in products if _squash(p)).most_common(1)[0][0]}
    return mine


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
    report["owner_facts"] = int((df["product_id"] == owner_id).sum())
    report["other_products"] = sorted({p for p in df["product_name"] if p != owner_name})
    return df, report


def save_facts(df: pd.DataFrame, owner_id: str, file_id: str, repo_dir: Path | None = None) -> tuple[Path, list[str]]:
    """Write data/repository/<owner_id>__<file_id>.csv. Returns (path, row warnings from validate_csv)."""
    folder = Path(repo_dir) if repo_dir else config.repo_dir()
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9-]+", "_", str(owner_id).lower()).strip("_") or "product"   # keep catalog ids readable
    path = folder / f"{safe}__{file_id}.csv"
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
    # Token diet without changing the teammates' file: pick the (cheap) extraction model and count every call.
    # Haiku 5.5 is 1/20 of Sonnet 5.5's price; every quote is still checked by code against the page text.
    if hasattr(conv, "CLAUDE_MODEL"):
        conv.CLAUDE_MODEL = config.extract_model()
    with usage.collect() as calls:
        rows, page_texts = conv.process_pdf(str(pdf_path), page_filter, usage.MeteredClient(client, "extract"),
                                            "claude", brand, False)
    df, report = to_facts(rows, owner_id, owner_name, family, brand)
    facts_path, warnings = save_facts(df, owner_id, file_id)
    save_pages(page_texts, file_id)
    report.update(csv=facts_path.name, warnings=warnings[:10], pages_sent=len(page_filter) if page_filter else len(page_texts),
                  model=config.extract_model(), cost=usage.total(calls))
    log(f"saved {report['facts']} facts ({report['ok']} ok, {report['unverified']} unverified) to {facts_path.name}")
    return report


def text_pages(text: str, size: int = 4000) -> list[str]:
    """Cut web-page text into 'pages' of about `size` characters, on line breaks (never mid-line)."""
    pages, current = [], ""
    for line in str(text or "").splitlines(keepends=True):
        if current and len(current) + len(line) > size:
            pages.append(current)
            current = ""
        current += line
    if current.strip():
        pages.append(current)
    return pages


def extract_text(text: str, name: str, owner_id: str, owner_name: str, family: str, brand: str, file_id: str,
                 pages: str = "", client=None, converter=None, log=print, workers: int = 8) -> dict:
    """Facts from web-page TEXT (the web import stores product pages as .txt; the converter only opens PDFs).
    Uses the converter's own PROMPT, call_with_retry and quote_ok, so the facts are checked the same way.
    'page' = the n-th ~4000-character section of the web page."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    conv = converter or load_converter()
    chunks = text_pages(text)
    page_filter = conv.parse_pages(pages) if pages.strip() else None
    todo = [(i, c) for i, c in enumerate(chunks, start=1) if (page_filter is None or i in page_filter)
            and len(c.strip()) >= 30]
    if client is None:
        import anthropic
        client = anthropic.Anthropic(timeout=90)
    if hasattr(conv, "CLAUDE_MODEL"):
        conv.CLAUDE_MODEL = config.extract_model()
    metered = usage.MeteredClient(client, "extract")
    prompt = conv.PROMPT.format(manufacturer=brand, product_family=owner_name, document_ref="")
    file_hash = hashlib.sha256(str(text).encode()).hexdigest()[:16]
    log(f"  {len(chunks)} sections of web-page text; {len(todo)} to send to the AI")

    def work(job):
        i, chunk = job
        try:
            return i, chunk, conv.call_with_retry(metered, "claude", prompt, chunk, None), None
        except Exception as e:  # noqa: BLE001 - one bad section must not stop the others
            return i, chunk, None, e

    rows = []
    with usage.collect() as calls, ThreadPoolExecutor(max_workers=workers) as ex:
        for done, (i, chunk, facts, err) in enumerate(ex.map(work, todo), start=1):
            if err:
                log(f"  section {i}: AI call failed: {err}")
                continue
            facts = [facts] if isinstance(facts, dict) else (facts or [])
            for f in facts:
                f.update({"page": i, "source_file": name, "file_hash": file_hash, "manufacturer": brand,
                          "product_family": owner_name, "quote_verified": conv.quote_ok(f.get("quote", ""), chunk)})
                rows.append(f)
            log(f"  [{done}/{len(todo)}] section {i}: {len(facts)} facts")
    df, report = to_facts(rows, owner_id, owner_name, family, brand)
    facts_path, warnings = save_facts(df, owner_id, file_id)
    save_pages([{"source_file": name, "page": i, "text": c} for i, c in enumerate(chunks, start=1)], file_id)
    report.update(csv=facts_path.name, warnings=warnings[:10], pages_sent=len(todo),
                  model=config.extract_model(), cost=usage.total(calls))
    log(f"saved {report['facts']} facts ({report['ok']} ok, {report['unverified']} unverified) to {facts_path.name}")
    return report
