"""
Test script: Danfoss datasheet PDF -> facts.csv (+ pages.csv)

Setup:
    pip install pdfplumber pymupdf pandas anthropic google-genai

    Option A (paid Claude key):  set ANTHROPIC_API_KEY=your-key
    Option B (free Gemini key):  set GEMINI_API_KEY=your-key

Run:
    python pdf_to_csv_test.py PVG_100.pdf
    python pdf_to_csv_test.py PVG_100.pdf PVG_128_and_256_Datasheet.pdf

Free Gemini version:
    python pdf_to_csv_test.py PVG_100.pdf --provider gemini

Check extraction only (no AI call, no key needed):
    python pdf_to_csv_test.py PVG_100.pdf --dry-run
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time

import fitz  # PyMuPDF
import pandas as pd
import pdfplumber

CLAUDE_MODEL = "claude-sonnet-5-5"   # change if your account uses another model
GEMINI_MODEL = "gemini-3.8-flash"    # if not found, pick a current Flash model in Google AI Studio

PROMPT = """You read one page of a Danfoss hydraulic datasheet (page text and page image).
Return ONLY a JSON list, no explanation, no markdown fences.
Each item has keys: product, attribute, value, unit, condition, quote, uncertain.

Rules:
- attribute: short snake_case name, e.g. max_pressure_port_P_continuous,
  rated_flow_port_AB, max_oil_temperature, max_sections.
- value: the number only, without a +/- sign (or a comma-separated list for things like control options).
- For +/- values (e.g. spool travel), put the number in value and write 'plus/minus' in condition.
- If a table has one column per product, make one item per product.
  If you cannot tell which product a value belongs to, use the sheet's
  product name and set uncertain=true.
- Table rows under a heading (e.g. 'Max. spool leakage at 100 bar...'):
  copy the heading's conditions into 'condition'.
- Facts about a product other than the one this sheet is about:
  set product to that product and uncertain=true.
- Ignore bracketed imperial values [psi, US gal/min, in]. Store metric only.
- quote: copied exactly from the page text.
- Only include facts that are on this page. If the page has no facts
  (drawing, graphs only, legal text), return [].
"""


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def quote_ok(quote, page_text):
    """True if the quote is on the page. Tolerant of two-column layouts where the
    PDF text comes out interleaved: every number must be on the page, and
    at least 90% of the words must be."""
    q, body = norm(quote), norm(page_text)
    if not q:
        return False
    if q in body:
        return True
    words = re.findall(r"[\w.%/\-]+", q)
    body_words = set(re.findall(r"[\w.%/\-]+", body))
    if not words:
        return False
    numbers = [w for w in words if re.search(r"\d", w)]
    if any(n not in body_words for n in numbers):
        return False
    return sum(w in body_words for w in words) / len(words) >= 0.9


def safe_save(write_fn, path):
    """Save a file; if it is open in Excel (locked), save under a new name instead."""
    try:
        write_fn(path)
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        alt = f"{base}_{time.strftime('%H%M%S')}{ext}"
        print(f"  '{path}' is open in another program (probably Excel). Saving as '{alt}' instead.")
        write_fn(alt)
        return alt


def page_image_b64(doc, i):
    pix = doc[i].get_pixmap(dpi=130)
    return base64.b64encode(pix.tobytes("png")).decode()


def clean_json(raw):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def call_llm(client, provider, page_text, image_b64):
    prompt = PROMPT + "\n\nPAGE TEXT:\n" + page_text
    if provider == "gemini":
        from google.genai import types
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=base64.b64decode(image_b64),
                                            mime_type="image/png"), prompt],
        )
        time.sleep(7)  # free tier allows only a few requests per minute
        return clean_json(resp.text)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return clean_json("".join(b.text for b in msg.content if b.type == "text"))


def call_with_retry(client, provider, text, image_b64, tries=6):
    """Retry when the AI service is busy (503) or rate-limited (429)."""
    wait = 15
    for attempt in range(1, tries + 1):
        try:
            return call_llm(client, provider, text, image_b64)
        except Exception as e:
            busy = any(code in str(e) for code in ("503", "429", "UNAVAILABLE", "overloaded", "timed out", "timeout", "Timeout", "DEADLINE"))
            if busy and attempt < tries:
                print(f"    service busy, waiting {wait}s and trying again ({attempt}/{tries - 1})...")
                time.sleep(wait)
                wait = min(wait * 2, 90)
            else:
                raise


def flag_conflicts(df):
    if df.empty:
        df["conflict"] = []
        return df
    key = ["product", "attribute", "condition"]
    df["condition"] = df["condition"].fillna("")
    counts = df.groupby(key)["value"].nunique()
    conflicted = set(counts[counts > 1].index)
    df["conflict"] = [tuple(r) in conflicted for r in df[key].values]
    return df


def process_pdf(path, client, provider, dry_run):
    file_hash = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    doc = fitz.open(path)
    rows, pages = [], []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            pages.append({"file_hash": file_hash, "source_file": os.path.basename(path),
                          "page": i + 1, "chars": len(text), "text": text})
            if len(text.strip()) < 30:
                print(f"  page {i+1}: almost no text (scan or image) - skipped, needs OCR")
                continue
            if dry_run:
                print(f"  page {i+1}: {len(text)} characters of text extracted")
                continue
            try:
                print(f"  page {i+1}: asking the AI (waits up to 90s)...")
                facts = call_with_retry(client, provider, text, page_image_b64(doc, i))
            except Exception as e:
                print(f"  page {i+1}: AI call or JSON parsing failed: {e}")
                continue
            body = norm(text)
            for f in facts:
                f["page"] = i + 1
                f["source_file"] = os.path.basename(path)
                f["file_hash"] = file_hash
                f["quote_verified"] = quote_ok(f.get("quote", ""), text)
                rows.append(f)
            print(f"  page {i+1}: {len(facts)} facts")
    return rows, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+")
    ap.add_argument("--provider", choices=["claude", "gemini"], default="claude")
    ap.add_argument("--model", help="override the AI model name, e.g. --model gemini-3.5-flash-lite")
    ap.add_argument("--out", default="", help="name tag for output files, e.g. --out haiku gives facts_haiku.csv")
    ap.add_argument("--dry-run", action="store_true", help="only test text extraction")
    args = ap.parse_args()
    tag = f"_{args.out}" if args.out else ""
    if args.model:
        globals()["GEMINI_MODEL" if args.provider == "gemini" else "CLAUDE_MODEL"] = args.model

    client = None
    if not args.dry_run:
        if args.provider == "gemini":
            if not os.environ.get("GEMINI_API_KEY"):
                sys.exit("Set GEMINI_API_KEY first, or use --dry-run.")
            from google import genai
            from google.genai import types as gtypes
            client = genai.Client(api_key=os.environ["GEMINI_API_KEY"],
                                  http_options=gtypes.HttpOptions(timeout=90000))  # give up after 90 s
        else:
            if not os.environ.get("ANTHROPIC_API_KEY"):
                sys.exit("Set ANTHROPIC_API_KEY first, or use --dry-run.")
            import anthropic
            client = anthropic.Anthropic(timeout=90)

    all_rows, all_pages = [], []
    for p in args.pdfs:
        if not os.path.exists(p):
            print(f"Skipping {p}: file not found in this folder (type 'dir' to see the real names)")
            continue
        print(f"Processing {p}")
        rows, pages = process_pdf(p, client, args.provider, args.dry_run)
        all_rows += rows
        all_pages += pages

    pages_path = safe_save(lambda f: pd.DataFrame(all_pages).to_csv(f, index=False, encoding="utf-8-sig"), f"pages{tag}.csv")
    print(f"Wrote {pages_path}")

    if args.dry_run:
        return

    cols = ["product", "attribute", "value", "unit", "condition", "page",
            "quote", "uncertain", "quote_verified", "source_file", "file_hash"]
    df = pd.DataFrame(all_rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = flag_conflicts(df[cols])
    facts_path = safe_save(lambda f: df.to_csv(f, index=False, encoding="utf-8-sig"), f"facts{tag}.csv")
    try:
        xlsx_path = safe_save(lambda f: df.to_excel(f, index=False), f"facts{tag}.xlsx")
        print(f"Also wrote {xlsx_path} (opens cleanly in Excel)")
    except ImportError:
        print("Tip: run  python -m pip install openpyxl  to also get an Excel file")

    print(f"\nWrote {facts_path}: {len(df)} rows")
    print("\nRows per page:")
    print(df.groupby(["source_file", "page"]).size().to_string())
    print(f"  quotes not found on page: {(~df['quote_verified'].astype(bool)).sum()}")
    print(f"  uncertain rows: {df['uncertain'].astype(str).str.lower().eq('true').sum()}")
    print(f"  conflicts: {df['conflict'].sum()}")


if __name__ == "__main__":
    main()
