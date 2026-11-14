"""
CUSTOMER FILES - feedback the customer gave us (PDF or Excel), used in the comparison and citable.

    save_customer_file("AGCO", "feedback.xlsx", data)   -> text passages saved as CSV (the file itself is NOT kept)
    customer_facts("AGCO", keywords)                     -> [Fact C1, C2, ...] the passages that matter most

Stored as   data/customers/<customer>/<file>.csv   with columns  customer, source_file, page, text
    PDF   -> one row per page          (page = page number)
    Excel -> one row per spreadsheet row (source_file = "file.xlsx [Sheet1]", page = row number)
    CSV / TXT -> one row per line / per file

The AI may cite these passages as C1, C2, ... (like F1, F2 for product facts). Code checks that every
number it writes about the customer really appears in the cited passage. Customer data is confidential:
the folder is in .gitignore and never committed.
"""
import io
import os
import re
from pathlib import Path

import pandas as pd

from . import config
from .facts import Fact

COLUMNS = ["customer", "source_file", "page", "text"]
ALLOWED = {"pdf", "xlsx", "xls", "csv", "txt"}
MAX_PASSAGE = 450            # characters per passage shown to the AI
SPEC_WORDS = ["bar", "l/min", "psi", "flow", "pressure", "rpm", "kg", "mm", "°c", "temperature", "noise", "leak",
              "life", "lifetime", "failure", "price", "cost", "delivery", "lead time", "service", "can", "section",
              "require", "need", "must", "problem", "complain", "issue", "want", "prefer"]


def customer_dir() -> Path:
    return Path(os.getenv("CUSTOMER_DIR", config.PROJECT_ROOT / "data" / "customers"))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")


# ---------------------------------------------------------------- 1. file -> rows of text
def file_to_rows(filename: str, data: bytes) -> list[dict]:
    """Extract text with its location. Raises ValueError for unsupported or unreadable files."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise ValueError(f"unsupported file type .{ext} (use PDF, Excel, CSV or TXT)")
    if ext == "pdf":
        import pdfplumber
        rows = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                for table in page.extract_tables() or []:
                    text += "\n" + "\n".join(" | ".join(str(c or "").strip() for c in r) for r in table)
                if text.strip():
                    rows.append({"source_file": filename, "page": i, "text": text.strip()})
        return rows
    if ext in ("xlsx", "xls"):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=str)
        return [row for name, df in sheets.items() for row in _table_rows(df, f"{filename} [{name}]")]
    if ext == "csv":
        return _table_rows(pd.read_csv(io.BytesIO(data), dtype=str), filename)
    text = data.decode("utf-8", errors="ignore")
    return [{"source_file": filename, "page": i, "text": line.strip()}
            for i, line in enumerate(text.splitlines(), start=1) if line.strip()]


def _table_rows(df: pd.DataFrame, source: str) -> list[dict]:
    """Each spreadsheet row -> 'Column: value; Column: value' (header row = 1, so data starts at row 2)."""
    df = df.dropna(how="all")
    out = []
    for idx, r in df.iterrows():
        parts = [f"{c}: {v}" for c, v in r.items() if isinstance(v, str) and v.strip() and not str(c).startswith("Unnamed")]
        if parts:
            out.append({"source_file": source, "page": int(idx) + 2, "text": "; ".join(parts)})
    return out


# ---------------------------------------------------------------- 2. save as CSV (only CSV is kept)
def save_customer_file(customer: str, filename: str, data: bytes) -> tuple[Path, int]:
    if not slug(customer):
        raise ValueError("name the customer")
    rows = file_to_rows(Path(filename).name, data)
    if not rows:
        raise ValueError("no text found (scanned PDF or empty sheet?)")
    folder = customer_dir() / slug(customer)
    folder.mkdir(parents=True, exist_ok=True)
    (customer_dir() / ".gitignore").write_text("*\n", encoding="utf-8")       # confidential: never committed
    path = folder / (Path(filename).stem + ".csv")
    pd.DataFrame([{"customer": customer, **r} for r in rows], columns=COLUMNS).to_csv(path, index=False)
    return path, len(rows)


def list_customers() -> dict[str, list[str]]:
    """customer name -> its files (for the website)."""
    out = {}
    folder = customer_dir()
    if not folder.exists():
        return out
    for csv in sorted(folder.glob("*/*.csv")):
        try:
            df = pd.read_csv(csv, dtype=str, nrows=1)
            name = df["customer"].iloc[0] if len(df) else csv.parent.name
        except Exception:  # noqa: BLE001
            continue
        out.setdefault(name, []).append(csv.stem)
    return out


# ---------------------------------------------------------------- 3. the passages that matter -> facts C1, C2 ...
def _passages(text: str) -> list[str]:
    """Split a page into short passages (paragraphs, then sentences) so the AI gets focused quotes."""
    out = []
    for para in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z])", text):
        para = " ".join(para.split())
        while len(para) > MAX_PASSAGE:
            cut = para.rfind(" ", 0, MAX_PASSAGE)
            out.append(para[:cut if cut > 0 else MAX_PASSAGE])
            para = para[cut if cut > 0 else MAX_PASSAGE:].strip()
        if len(para) > 15:
            out.append(para)
    return out


def customer_facts(customer: str, keywords: list[str] = (), max_items: int = 12) -> list[Fact]:
    """The customer's most relevant passages as facts C1..Cn (empty list if we have no files)."""
    folder = customer_dir() / slug(customer)
    if not customer or not folder.exists():
        return []
    words = [w.lower() for w in list(SPEC_WORDS) + [k for k in keywords if len(k) > 2]]
    scored = []
    for csv in sorted(folder.glob("*.csv")):
        df = pd.read_csv(csv, dtype=str, keep_default_na=False)
        for _, row in df.iterrows():
            for p in _passages(row["text"]):
                low = p.lower()
                score = sum(low.count(w) for w in words) + 2 * len(re.findall(r"\d", low)) ** 0.5
                scored.append((score, len(scored), row["source_file"], row["page"], p))
    best = sorted(sorted(scored, key=lambda x: -x[0])[:max_items], key=lambda x: x[1])   # keep reading order
    return [Fact(id=f"C{i}", product_id=f"customer:{slug(customer)}", role="customer", attribute="customer_note",
                 value=text, unit=None, norm_value=None, norm_unit=None, source_file=src,
                 page=int(page) if str(page).isdigit() else None, quote=text, status="ok", note=customer)
            for i, (_, _, src, page, text) in enumerate(best, start=1)]
