"""
Datasheet PDF -> facts CSV/Excel.  Works for Danfoss AND competitor datasheets.

Setup (once):
    python -m pip install pdfplumber pymupdf pandas anthropic google-genai openpyxl

    Claude key:   set ANTHROPIC_API_KEY=your-key
    Gemini key:   set GEMINI_API_KEY=your-key      (then add --provider gemini)

Run:
    python pdf_to_csv_test.py file1.pdf file2.pdf --out mytest

Limit which pages are sent to the AI by adding :pages after a file name
(useful for long datasheets; the text of ALL pages is still saved):
    python pdf_to_csv_test.py "M4-15.pdf:1,3-4,8-11,15-21"

Other options:
    --company "Bosch Rexroth"   tell the script who makes the files (skips guessing)
    --model <name>              use another AI model
    --out <tag>                 output names, e.g. --out walvoil -> facts_walvoil.xlsx
    --dry-run                   only test PDF reading (no AI call, no key needed)
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

ID_PROMPT = """You see the first page of a hydraulic product datasheet or brochure (page text and page image).
Return ONLY a JSON object (no explanation, no markdown) with these keys:
manufacturer, product_family, document_ref, edition_date
- manufacturer: the company that makes the product, e.g. Danfoss, Walvoil, Bosch Rexroth.
  Use the logo, footer or page text.
- product_family: the main product name or series on the cover, e.g. 'PVG 100', 'DPX-HS', 'M4-15'.
- document_ref: the document or publication number if printed, else ''.
- edition_date: the edition or date if printed, else ''.
Use '' for anything you cannot see. Do not guess."""

PROMPT = """You read ONE page of a hydraulic product datasheet or brochure (page text and page image).
Document: {manufacturer} {product_family} (ref {document_ref})
Return ONLY a JSON list, no explanation, no markdown fences.
Each item has keys: product, variant, attribute, value, unit, condition, fact_type, quote, uncertain.

What matters most: flow, pressure (by port), displacement, number of sections, size,
dimensions and weight, ports and connections, actuation and control options, temperature,
viscosity, filtration, ordering/model-code structure, typical applications, performance claims.

Rules:
- product: the specific product the value is about, as the sheet names it (e.g. 'PVG 256', 'DPX100').
  Never the manufacturer's name.
- variant: size, plate, spool or version if the value applies only to that one
  (e.g. 'inlet plate JZ', 'pressure compensator S'); otherwise ''.
- A table with one column per product, size or version: make one item per column.
- attribute: short snake_case name that includes the port or part when relevant,
  e.g. rated_flow_port_P, max_pressure_port_AB, back_pressure_port_T, max_oil_temperature.
- value: the number only, without a +/- sign. Lists as comma-separated text.
  Ranges as printed, e.g. '-20 to 100'.
- For +/- values write 'plus/minus' in condition.
- Units: if both metric and imperial are given, store ONLY metric (bar, l/min, mm, kg, degrees C,
  cSt or mm2/s). This also applies when imperial comes first and metric is in brackets,
  e.g. '5100 psi (350 bar)' means 350 bar. If only imperial is given, store it as printed with its unit.
- condition: any heading, test condition or note the value depends on
  (e.g. 'stand-by 14 bar', 'at 100 bar and 21 mm2/s', 'with shock valve', 'intermittent',
  'depends on spool control type').
- fact_type: one of
  spec (rated or measured technical data),
  feature (capability, option, list of options, application),
  claim (marketing or performance claim, e.g. 'up to 20% productivity'),
  ordering_code (model-code structure),
  dimension (labelled size or weight).
- If you cannot tell which product, column or condition a value belongs to, set uncertain=true.
  If the table layout is unclear, also set uncertain=true.
- Facts about OTHER products mentioned in passing: product = that product, uncertain=true.
- Ordering-code tables: one item per code position, attribute 'ordering_code_<field>',
  value = the options with their meaning joined by ';' (keep it short).
- Dimension drawings: ONLY extract a dimension if the page text itself names what it measures (e.g. 'Length L = 152 mm'). Loose numbers around a drawing with no label in the text: do NOT extract them at all. Never use a pair like '127.5 5.02' as a quote.
- Ignore chart and graph axis numbers, legends, and port or symbol letters in circuit diagrams.
  Only use values stated in running text or in tables.
- quote: copied exactly from the page text, at most about 20 words.
- Skip legal text, contact details, connector part numbers and marketing fluff without facts.
- Only facts on this page. If there are none, return [].
"""

COLUMNS = ["manufacturer", "product_family", "product", "variant", "attribute", "value", "unit",
           "condition", "fact_type", "page", "quote", "uncertain", "quote_verified",
           "source_file", "document_ref", "edition_date", "file_hash"]


def norm(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def squash(s):
    """Lower-case, unify minus signs and ellipsis, remove ALL spaces (PDF text often
    has missing or extra spaces)."""
    s = str(s).lower()
    for a in "\u2212\u2013\u2014\u2012":
        s = s.replace(a, "-")
    s = s.replace("\u2026", "...")
    return re.sub(r"[\s\x00-\x1f]+", "", s)   # also strips odd control characters used as spaces


def quote_ok(quote, page_text):
    """True if the quote is on the page. Ignores spaces, minus-sign and ellipsis variants.
    Tolerant of two-column layouts where the PDF text comes out interleaved:
    every number must be on the page, and at least 90% of the words must be."""
    q, body = squash(quote), squash(page_text)
    if not q:
        return False
    if q in body:
        return True
    # some PDFs draw bold text twice, so the text layer reads 'lliiffttiinngg': compare with doubled letters collapsed
    # only runs of 3+ doubled letters are collapsed, so normal words like 'Walvoil' are left alone
    body2 = re.sub(r"((?:(.)\2){3,})", lambda m: m.group(1)[::2], body)
    if q in body2:
        return True
    # numbers are compared on the text WITH its spaces (squashing would glue numbers together)
    nums = re.findall(r"\d+(?:\.\d+)?", str(quote))
    body_nums = set(re.findall(r"\d+(?:\.\d+)?", str(page_text)))
    if any(n not in body_nums for n in nums):
        return False
    words = re.findall(r"[^\W\d_]{3,}", str(quote).lower())
    if not words:
        return bool(nums)
    return sum(w in body for w in words) / len(words) >= 0.9


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


def get_text(pdf, i):
    """Page text with odd control characters (some PDFs use them as spaces) turned into spaces."""
    text = pdf.pages[i].extract_text() or ""
    return re.sub(r"[\x00-\x08\x0b-\x1f]", " ", text)


def page_image_b64(doc, i):
    pix = doc[i].get_pixmap(dpi=130)
    return base64.b64encode(pix.tobytes("png")).decode()


def parse_pages(spec):
    pages = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            pages.update(range(int(a), int(b) + 1))
        else:
            pages.add(int(part))
    return pages


def parse_target(arg):
    """'file.pdf' or 'file.pdf:1,3-4' -> (path, set of pages or None)"""
    m = re.match(r"^(.*):([\d,\-\s]+)$", arg)
    if m:
        return m.group(1), parse_pages(m.group(2))
    return arg, None


def clean_json(raw):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def call_llm(client, provider, prompt, page_text, image_b64):
    full_prompt = prompt + "\n\nPAGE TEXT:\n" + page_text
    if provider == "gemini":
        from google.genai import types
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Part.from_bytes(data=base64.b64decode(image_b64),
                                            mime_type="image/png"), full_prompt],
        )
        time.sleep(7)  # free tier allows only a few requests per minute
        return clean_json(resp.text)
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
                {"type": "text", "text": full_prompt},
            ],
        }],
    )
    return clean_json("".join(b.text for b in msg.content if b.type == "text"))


def call_with_retry(client, provider, prompt, text, image_b64, tries=6):
    """Retry when the AI service is busy / rate-limited / times out, and once or twice
    when the answer was not valid JSON."""
    wait, bad_json = 15, 0
    for attempt in range(1, tries + 1):
        try:
            return call_llm(client, provider, prompt, text, image_b64)
        except ValueError as e:                      # includes json.JSONDecodeError
            bad_json += 1
            if bad_json <= 2 and attempt < tries:
                print("    answer was not valid JSON, trying again...")
                time.sleep(2)
            else:
                raise
        except Exception as e:
            busy = any(code in str(e) for code in
                       ("503", "429", "UNAVAILABLE", "overloaded", "timed out", "timeout",
                        "Timeout", "DEADLINE"))
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
    key = ["manufacturer", "product", "variant", "attribute", "condition"]
    for c in key:
        df[c] = df[c].fillna("").astype(str)
    # only numeric facts can truly conflict; descriptive ones (features, claims, application lists) may have many values
    numeric = df["fact_type"].isin(["spec", "dimension"]) & df["value"].astype(str).str.contains(r"\d")
    counts = df[numeric].groupby(key)["value"].nunique()
    conflicted = set(counts[counts > 1].index)
    df["conflict"] = [bool(n) and tuple(r) in conflicted for n, r in zip(numeric, df[key].values)]
    return df


def identify_document(client, provider, doc, pdf, path, company):
    """Ask the AI who made the document and what product it covers (one cheap call)."""
    info = {"manufacturer": "", "product_family": "", "document_ref": "", "edition_date": ""}
    try:
        text = get_text(pdf, 0)
        got = call_with_retry(client, provider, ID_PROMPT, text, page_image_b64(doc, 0))
        if isinstance(got, list) and got:
            got = got[0]
        if isinstance(got, dict):
            for k in info:
                info[k] = str(got.get(k, "") or "")
    except Exception as e:
        print(f"  could not identify the document automatically: {e}")
    if company:
        info["manufacturer"] = company
    if not info["manufacturer"]:
        info["manufacturer"] = "UNKNOWN"
    return info


def process_pdf(path, page_filter, client, provider, company, dry_run):
    file_hash = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    name = os.path.basename(path)
    doc = fitz.open(path)
    rows, pages = [], []
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        todo = [i for i in range(n) if page_filter is None or (i + 1) in page_filter]
        print(f"  {n} pages in the file; asking the AI about {0 if dry_run else len(todo)} of them")

        info = {"manufacturer": company or "", "product_family": "", "document_ref": "", "edition_date": ""}
        if not dry_run:
            info = identify_document(client, provider, doc, pdf, path, company)
            print(f"  detected: {info['manufacturer']} | {info['product_family']} | "
                  f"{info['document_ref']} | {info['edition_date']}")
        prompt = PROMPT.format(manufacturer=info["manufacturer"], product_family=info["product_family"],
                               document_ref=info["document_ref"])

        for i in range(n):
            text = get_text(pdf, i)
            pages.append({"manufacturer": info["manufacturer"], "source_file": name,
                          "file_hash": file_hash, "page": i + 1, "chars": len(text), "text": text})
            if i not in todo:
                continue
            if len(text.strip()) < 30:
                print(f"  page {i+1}: almost no text (scan or image) - skipped, needs OCR")
                continue
            if dry_run:
                print(f"  page {i+1}: {len(text)} characters of text extracted")
                continue
            print(f"  page {i+1}: asking the AI (waits up to 90s)...")
            try:
                facts = call_with_retry(client, provider, prompt, text, page_image_b64(doc, i))
            except Exception as e:
                print(f"  page {i+1}: AI call or JSON parsing failed: {e}")
                continue
            if isinstance(facts, dict):
                facts = [facts]
            for f in facts:
                f["page"] = i + 1
                f["source_file"] = name
                f["file_hash"] = file_hash
                f["manufacturer"] = info["manufacturer"]
                f["product_family"] = info["product_family"]
                f["document_ref"] = info["document_ref"]
                f["edition_date"] = info["edition_date"]
                f["quote_verified"] = quote_ok(f.get("quote", ""), text)
                rows.append(f)
            print(f"  page {i+1}: {len(facts)} facts")
    return rows, pages


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdfs", nargs="+", help="PDF files; add :1,3-4 after a name to limit pages")
    ap.add_argument("--provider", choices=["claude", "gemini"], default="claude")
    ap.add_argument("--model", help="override the AI model name")
    ap.add_argument("--company", default="", help="who makes the files, e.g. --company Walvoil")
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
    for arg in args.pdfs:
        path, page_filter = parse_target(arg)
        if not os.path.exists(path):
            print(f"Skipping {path}: file not found in this folder (type 'dir' to see the real names)")
            continue
        print(f"Processing {path}")
        rows, pages = process_pdf(path, page_filter, client, args.provider, args.company, args.dry_run)
        all_rows += rows
        all_pages += pages

    pages_path = safe_save(lambda f: pd.DataFrame(all_pages).to_csv(f, index=False, encoding="utf-8-sig"),
                           f"pages{tag}.csv")
    print(f"Wrote {pages_path}")

    if args.dry_run:
        return

    df = pd.DataFrame(all_rows)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLUMNS]
    df["value"] = df["value"].astype(str)
    df = flag_conflicts(df)
    facts_path = safe_save(lambda f: df.to_csv(f, index=False, encoding="utf-8-sig"), f"facts{tag}.csv")
    try:
        xlsx_path = safe_save(lambda f: df.to_excel(f, index=False), f"facts{tag}.xlsx")
        print(f"Also wrote {xlsx_path} (opens cleanly in Excel)")
    except ImportError:
        print("Tip: run  python -m pip install openpyxl  to also get an Excel file")

    print(f"\nWrote {facts_path}: {len(df)} rows")
    print(f"  quotes not found on page: {(~df['quote_verified'].astype(bool)).sum()}")
    print(f"  uncertain rows: {df['uncertain'].astype(str).str.lower().eq('true').sum()}")
    print(f"  conflicts: {df['conflict'].sum()}")
    print("\nRows per file and page:")
    print(df.groupby(["source_file", "page"]).size().to_string())
    print("\nRows per fact type:")
    print(df["fact_type"].value_counts().to_string())


if __name__ == "__main__":
    main()
