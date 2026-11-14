"""
DOCUMENTS - where each source document can be opened online (filled by the datasheet crawler).

We only keep CSVs, not the PDFs themselves. So every source in the result links to the ORIGINAL document
on the manufacturer's website, opened at the cited page:  https://.../datasheet.pdf#page=12

    data/documents.csv   (one row per document)
    source_file , url , title , revision , date , found_by , checked_at
    BC152886483664en-001602.pdf , https://assets.danfoss.com/... , PVG 32 Technical Information , 001602 , 2024-07 , crawler , 2026-11-14

    link("BC152886483664en-001602.pdf", 12)  -> {"url": "https://...pdf#page=12", "title": ..., "date": ...}

`source_file` must be written exactly as in the facts CSVs. The crawler (teammate) writes this file;
validate_documents(df) checks it before saving. A document without a url simply has no link yet.
"""
import os
from pathlib import Path

import pandas as pd

from . import config
from .units import is_empty

COLUMNS = ["source_file", "url", "title", "revision", "date", "found_by", "checked_at"]
_CACHE: dict = {}


def documents_file() -> Path:
    return Path(os.getenv("DOCUMENTS_FILE", config.PROJECT_ROOT / "data" / "documents.csv"))


def validate_documents(df: pd.DataFrame) -> list[str]:
    problems = [f"missing column: {c}" for c in ("source_file", "url") if c not in df.columns]
    if problems:
        return problems
    for i, row in df.iterrows():
        url = "" if is_empty(row.get("url")) else str(row["url"]).strip()
        if is_empty(row.get("source_file")):
            problems.append(f"line {i + 2}: source_file is empty")
        if url and not url.lower().startswith(("https://", "http://")):
            problems.append(f"line {i + 2}: url must start with http(s)://")
    dupes = df["source_file"][df["source_file"].duplicated()].tolist()
    problems += [f"source_file listed twice: {d}" for d in dupes]
    return problems


def load_documents(path: Path | None = None) -> dict[str, dict]:
    """source_file -> {url, title, revision, date, ...}. Missing/broken file -> {} (links are a bonus)."""
    path = Path(path) if path else documents_file()
    if not path.exists():
        return {}
    key = (str(path), path.stat().st_mtime_ns)
    if _CACHE.get("key") != key:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False)
            ok = not validate_documents(df)
            docs = {r["source_file"].strip(): {c: (r.get(c) or "").strip() for c in COLUMNS}
                    for _, r in df.iterrows()} if ok else {}
        except Exception:  # noqa: BLE001 - a broken file must not break the comparison
            docs = {}
        _CACHE.update(key=key, docs=docs)
    return _CACHE["docs"]


def link(source_file: str | None, page=None, path: Path | None = None) -> dict | None:
    """Online link for a source, opened at the page when it is a PDF. None if no url is known."""
    doc = load_documents(path).get((source_file or "").strip())
    if not doc or not doc.get("url"):
        return None
    url = doc["url"]
    if page and ".pdf" in url.lower() and "#" not in url:
        url += f"#page={page}"
    return {"url": url, "title": doc.get("title") or source_file,
            "date": " · ".join(x for x in (doc.get("revision"), doc.get("date")) if x)}


def save_document(source_file: str, url: str, title: str = "", found_by: str = "scraper",
                  path: Path | None = None) -> None:
    """Add or update one document's online link (e.g. after a web import). Other rows stay as they are."""
    from datetime import date
    path = Path(path) if path else documents_file()
    df = pd.read_csv(path, dtype=str, keep_default_na=False) if path.exists() else pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    row = {"source_file": source_file, "url": url, "title": title or source_file, "found_by": found_by,
           "checked_at": date.today().isoformat()}
    hit = df["source_file"] == source_file
    if hit.any():
        for k, v in row.items():
            df.loc[hit, k] = v
    else:
        df = pd.concat([df, pd.DataFrame([{c: row.get(c, "") for c in COLUMNS}])], ignore_index=True)
    problems = validate_documents(df)
    if problems:
        raise ValueError("; ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    df[COLUMNS].to_csv(path, index=False)
