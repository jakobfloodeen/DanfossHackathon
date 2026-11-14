"""
Backend for the Competitor Intelligence Assistant front end.

Implements the 5 endpoints from frontend/README.md and serves the front end itself,
so everything runs on one address:  http://localhost:8000

Run:   uvicorn backend:app --reload
"""

import hashlib
import io
import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pdfplumber
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

BASE = Path(__file__).parent
FRONTEND_DIR = BASE / "frontend"
DATA_DIR = BASE / "data"
FILES_DIR = DATA_DIR / "files"          # original uploads
EXTRACT_DIR = DATA_DIR / "extracted"    # page-by-page text, one JSON per file
CATALOG_FILE = DATA_DIR / "catalog.json"
for d in (FILES_DIR, EXTRACT_DIR):
    d.mkdir(parents=True, exist_ok=True)

MODEL = os.getenv("ASK_MODEL", "claude-haiku-5-5")   # chat-box questions: short answers from page text
ALLOWED_EXT = {"pdf", "csv", "xlsx", "xls", "json", "txt", "html", "htm", "docx"}
MAX_MB = 50

app = FastAPI(title="Competitor Intelligence Assistant API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# =====================================================================
# Catalog storage (the front end's single source of truth)
# =====================================================================
def load_catalog() -> dict:
    if not CATALOG_FILE.exists():
        seed = FRONTEND_DIR / "examples" / "catalog.json"
        cat = json.loads(seed.read_text(encoding="utf-8"))
        # The example lists datasets that aren't actually on disk: start clean.
        for n in walk(cat["tree"]):
            n.pop("datasets", None)
            if n.get("type") == "product":
                n["status"] = "data-pending"
                n["desc"] = "No datasheet uploaded yet"
        cat["competitorProducts"] = []
        for c in cat["competitors"]:
            c["hasData"] = False
        cat["version"] = "1.0"
        save_catalog(cat)
    return json.loads(CATALOG_FILE.read_text(encoding="utf-8"))


def save_catalog(cat: dict) -> None:
    CATALOG_FILE.write_text(json.dumps(cat, indent=2, ensure_ascii=False), encoding="utf-8")


def walk(nodes):
    for n in nodes:
        yield n
        yield from walk(n.get("children", []))


def find_node(cat, node_id):
    return next((n for n in walk(cat["tree"]) if n["id"] == node_id), None)


def find_parent(cat, node_id):
    for n in walk(cat["tree"]):
        if any(c["id"] == node_id for c in n.get("children", [])):
            return n
    return None


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "item"


def issue(level, text):
    return {"level": level, "text": text}


# =====================================================================
# Text extraction (keeps page numbers so every fact can be cited)
# =====================================================================
def extract_pages(path: Path, ext: str) -> list[dict]:
    if ext == "pdf":
        pages = []
        with pdfplumber.open(path) as pdf:
            for i, p in enumerate(pdf.pages, start=1):
                text = p.extract_text() or ""
                for t in p.extract_tables() or []:
                    rows = [" | ".join(str(c or "").strip() for c in row) for row in t]
                    text += "\n[TABLE]\n" + "\n".join(rows)
                pages.append({"page": i, "text": text})
        return pages
    if ext in ("csv", "xlsx", "xls"):
        import pandas as pd
        df = pd.read_csv(path) if ext == "csv" else pd.read_excel(path)
        return [{"page": 1, "text": df.to_csv(index=False)}]
    if ext in ("txt", "json", "html", "htm"):
        return [{"page": 1, "text": path.read_text(encoding="utf-8", errors="ignore")}]
    return []  # docx etc.: stored, not extracted yet


def pages_for(datasets: list[dict]) -> list[dict]:
    """All extracted pages of a product's datasets: [{fileId, doc, page, text}]"""
    out = []
    for ds in datasets or []:
        f = EXTRACT_DIR / f"{ds.get('fileId')}.json"
        if f.exists():
            for p in json.loads(f.read_text(encoding="utf-8")):
                out.append({"fileId": ds["fileId"], "doc": ds["name"], "page": p["page"], "text": p["text"]})
    return out


SPEC_WORDS = ["bar", "l/min", "psi", "mm", "kg", "°c", "technical data", "max", "pressure", "flow",
              "port", "iso 4406", "can", "section", "weight", "temperature", "viscosity", "thread",
              "unf", "voltage", "spool", "specification", "dimension"]


def select_pages(pages, extra_words=(), limit=25, max_chars=60000):
    """Pick the pages most likely to hold specs (a datasheet can have 200+ pages)."""
    words = [w.lower() for w in list(SPEC_WORDS) + list(extra_words) if w]
    scored = []
    for p in pages:
        t = p["text"].lower()
        score = sum(t.count(w) for w in words) + (20 if "technical data" in t else 0)
        if t.strip():
            scored.append((score, p))
    top = [p for _, p in sorted(scored, key=lambda x: -x[0])[:limit]]
    top.sort(key=lambda p: (p["doc"], p["page"]))
    picked, total = [], 0
    for p in top:
        if total + len(p["text"]) > max_chars:
            break
        picked.append(p)
        total += len(p["text"])
    return picked


def pages_as_prompt(label, pages):
    parts = [f"<page doc=\"{p['doc']}\" file_id=\"{p['fileId']}\" page=\"{p['page']}\">\n{p['text']}\n</page>"
             for p in pages]
    return f"<product name=\"{label}\">\n" + "\n".join(parts) + "\n</product>"


# =====================================================================
# GET /api/catalog
# =====================================================================
@app.get("/api/catalog")
def get_catalog():
    cat = load_catalog()
    cat["customers"] = [{"name": n, "files": f} for n, f in list_customers().items()]   # customer feedback on file
    return cat


# =====================================================================
# POST /api/catalog/nodes
# =====================================================================
class NodeIn(BaseModel):
    parentId: Optional[str] = None
    label: str = ""
    desc: str = ""
    type: str = "group"


@app.post("/api/catalog/nodes")
def create_node(body: NodeIn):
    cat = load_catalog()
    parent = find_node(cat, body.parentId) if body.parentId else None
    if body.parentId and not parent:
        return {"node": None, "issues": [issue("error", "Parent group not found.")]}
    if parent and parent.get("type") == "product":
        return {"node": None, "issues": [issue("error", "A product cannot contain other items.")]}
    name = body.label.strip()
    if not name:
        return {"node": None, "issues": [issue("error", "Name is required.")]}
    siblings = parent.setdefault("children", []) if parent else cat["tree"]
    if any(n["label"].lower() == name.lower() for n in siblings):
        return {"node": None, "issues": [issue("conflict", f'"{name}" already exists at this level.')]}

    node_id = slug(name)
    while find_node(cat, node_id):
        node_id += "-2"
    node = {"id": node_id, "label": name, "desc": body.desc.strip(), "status": "data-pending", "datasets": []}
    if body.type == "product":
        node["type"] = "product"
    else:
        node.update(prompt="Choose one:", children=[])
    if parent and parent.get("status") == "coming-soon":
        parent["status"] = "active"
    siblings.append(node)
    save_catalog(cat)

    issues = [issue("ok", f'Added "{name}".')]
    if not node["desc"]:
        issues.append(issue("warning", "No description given; add one so users can tell items apart."))
    return {"node": node, "issues": issues}


# =====================================================================
# POST /api/datasets  (multipart upload)
# =====================================================================
@app.post("/api/datasets")
async def upload_datasets(
    source: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    nodeId: Optional[str] = Form(None),
    competitorId: Optional[str] = Form(None),
    competitorName: Optional[str] = Form(None),
    productName: Optional[str] = Form(None),
    categoryId: Optional[str] = Form(None),
    equivalentOf: Optional[str] = Form(None),
    customerName: Optional[str] = Form(None),
):
    if not files:
        return {"uploaded": [], "issues": [issue("error", "No file was received. Choose a file first, then click Upload.")]}
    if source == "customer":                     # customer feedback: only the extracted text is kept (as CSV)
        return await upload_customer_files(customerName, files)
    cat = load_catalog()
    found = resolve_target(cat, source, nodeId, competitorId, competitorName, productName, categoryId, equivalentOf)
    if "error" in found:
        return {"uploaded": [], "issues": found["issues"]}
    issues, stored = found["issues"], []
    for f in files:
        ds = store_file(found, Path(f.filename).name, await f.read(), issues)
        if ds:
            stored.append(ds)
    finish_upload(cat, found, stored, issues)
    return {"uploaded": [d["name"] for d in stored], "issues": issues,
            "datasets": [{"ownerId": found["owner_id"], "fileId": d["fileId"], "name": d["name"]} for d in stored]}


def resolve_target(cat, source, nodeId=None, competitorId=None, competitorName=None, productName=None,
                   categoryId=None, equivalentOf=None) -> dict:
    """Which catalog item the files belong to (creates a competitor / competitor product if needed).
    Returns {target, comp, label, owner_id, source, issues} or {error, issues}."""
    issues = []
    if source == "competitor":
        comp = next((c for c in cat["competitors"] if c["id"] == competitorId), None)
        new_name = (competitorName or "").strip()
        if not comp and new_name:
            comp = next((c for c in cat["competitors"] if c["name"].lower() == new_name.lower()), None)
            if comp:
                issues.append(issue("warning", f'"{new_name}" already exists as a competitor; files attached to it.'))
            else:
                comp = {"id": slug(new_name), "name": new_name, "hasData": False}
                cat["competitors"].append(comp)
                issues.append(issue("ok", f'Added competitor "{new_name}".'))
        if not comp:
            return {"error": True, "issues": [issue("error", "Choose or name a competitor.")]}
        pname = (productName or "").strip()
        if not pname:
            return {"error": True, "issues": [issue("error", "Name the competitor product or series.")]}
        category = find_node(cat, categoryId)
        if not category or category.get("type") == "product":
            return {"error": True, "issues": [issue("error", "Choose the product category it competes in.")]}

        target = next((p for p in cat["competitorProducts"]
                       if p["competitorId"] == comp["id"] and p["name"].lower() == pname.lower()), None)
        if target:
            issues.append(issue("warning", f"{comp['name']} {pname} already exists; files added to it."))
        else:
            target = {"id": f"{comp['id']}-{slug(pname)}", "competitorId": comp["id"], "name": pname,
                      "categoryId": category["id"], "equivalentOf": [], "datasets": [], "status": "pending"}
            cat["competitorProducts"].append(target)
            issues.append(issue("ok", f"Added competitor product {comp['name']} {pname} under {category['label']}."))
        if equivalentOf and equivalentOf not in target["equivalentOf"]:
            target["equivalentOf"].append(equivalentOf)
            issues.append(issue("ok", "Saved as a confirmed equivalent pair (also used to test matching accuracy)."))
        label, owner_id = f"{comp['name']} {pname}", target["id"]
    else:
        target = find_node(cat, nodeId)
        if not target:
            return {"error": True, "issues": [issue("error", "Target product or group not found.")]}
        label, owner_id, comp = target["label"], target["id"], None
    target.setdefault("datasets", [])
    return {"target": target, "comp": comp, "label": label, "owner_id": owner_id, "source": source, "issues": issues}


def store_file(found: dict, name: str, data: bytes, issues: list) -> Optional[dict]:
    """Check, store and text-extract ONE file for the resolved target. Returns the dataset entry or None."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        issues.append(issue("error", f"{name}: unsupported file type (.{ext}).")); return None
    if not data:
        issues.append(issue("error", f"{name}: file is empty.")); return None
    if len(data) > MAX_MB * 1024 * 1024:
        issues.append(issue("error", f"{name}: larger than {MAX_MB} MB.")); return None

    file_id = hashlib.sha256(data).hexdigest()[:16]
    folder = FILES_DIR / found["owner_id"]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}_{name}"
    path.write_bytes(data)

    try:
        pages = extract_pages(path, ext)
    except Exception as e:  # broken file, missing openpyxl, ...
        pages = []
        issues.append(issue("warning", f"{name}: stored, but text extraction failed ({e})."))
    (EXTRACT_DIR / f"{file_id}.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")

    chars = sum(len(p["text"].strip()) for p in pages)
    if ext == "pdf" and chars < 200:
        issues.append(issue("warning", f"{name}: almost no text found; it may be a scanned PDF."))
    elif not pages:
        issues.append(issue("warning", f"{name}: stored, but this file type isn't extracted yet."))

    target, comp = found["target"], found["comp"]
    target["datasets"] = [d for d in target["datasets"] if d["name"] != name]
    ds = {"name": name, "fileId": file_id, "source": found["source"],
          "competitorId": comp["id"] if comp else None,
          "pages": len(pages), "uploadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    target["datasets"].append(ds)
    return ds


def finish_upload(cat, found: dict, stored: list, issues: list) -> None:
    target, comp = found["target"], found["comp"]
    if stored:
        target["status"] = "active"
        if found["source"] == "danfoss" and target.get("desc", "").startswith("No datasheet"):
            target["desc"] = f"{len(target['datasets'])} dataset(s) loaded"
        if comp:
            comp["hasData"] = True
        issues.append(issue("ok", f"{len(stored)} file(s) for {found['label']} stored and text extracted. "
                                  "Click 'Extract facts' to turn a PDF into comparable facts."))
    save_catalog(cat)


# =====================================================================
# Claude helpers
# =====================================================================
def claude():
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    from comparison_engine import usage
    return usage.MeteredClient(anthropic.Anthropic(), "ask")




RULES = """You compare hydraulic products for Danfoss sales teams.
Rules:
- Use ONLY the <page> text provided. Never use outside knowledge for values.
- Every non-null value and every insight must cite sources as [{"file_id": "...", "page": N}] taken from the <page> tags it came from.
- status per value: ok | conflict (sources disagree; explain in note) | missing (value null) | inferred (derived, explain) | converted (unit converted, e.g. psi->bar; keep the original in note).
- Prefer metric units (bar, l/min, mm, kg, °C).
- Compare like with like (e.g. continuous vs intermittent pressure) and say so in the note when unclear.
- Add a flag for every conflict, missing must-have, and anything a salesperson must double-check.
- Benefits are for the named buyer and application; trade-offs are what changes if they switch.
- Do not invent prices, lead times or service life. Mark them missing unless a page states them."""


# =====================================================================
# Comparison engine (comparison_engine/): sourced facts in, checked AI interpretation out
# =====================================================================
from comparison_engine import interpret, load_repository      # noqa: E402
from comparison_engine.website import to_website              # noqa: E402
from comparison_engine.documents import link as document_link  # noqa: E402
from comparison_engine.customer import customer_facts, list_customers, save_customer_file  # noqa: E402
from comparison_engine.system import System, compare_systems, compose_system  # noqa: E402
from comparison_engine import usage                                              # noqa: E402
from comparison_engine import commercial                                          # noqa: E402


async def upload_customer_files(customer_name: Optional[str], files: list[UploadFile]) -> dict:
    """Customer feedback (PDF / Excel / CSV / TXT): text -> data/customers/<customer>/<file>.csv.
    The original file is NOT stored. Used automatically when the brief's buyer company matches."""
    name = (customer_name or "").strip()
    if not name:
        return {"uploaded": [], "issues": [issue("error", "Name the customer the feedback comes from.")]}
    uploaded, issues = [], []
    for f in files:
        data = await f.read()
        fname = Path(f.filename).name
        if not data:
            issues.append(issue("error", f"{fname}: file is empty.")); continue
        if len(data) > MAX_MB * 1024 * 1024:
            issues.append(issue("error", f"{fname}: larger than {MAX_MB} MB.")); continue
        try:
            _, rows = save_customer_file(name, fname, data)
        except Exception as e:  # unsupported type, unreadable, scanned PDF ...
            issues.append(issue("error", f"{fname}: {e}")); continue
        uploaded.append(fname)
        issues.append(issue("ok", f"{fname}: {rows} text passage(s) saved for {name} (original file not stored)."))
    if uploaded:
        issues.append(issue("ok", f"Used automatically when the buyer in a comparison is {name}."))
    return {"uploaded": uploaded, "issues": issues}

ENGINE_ASK = None   # None = the real Claude API. Tests put a fake function here (no key, no cost).
WAITING = "Datasheet uploaded - values not extracted to the facts CSV yet."


def products_with_facts() -> set[str]:
    """Product ids that have at least one row in the facts CSVs (data/repository/)."""
    data = load_repository()
    return set(data["product_id"]) if not data.empty else set()


def catalog_names(cat) -> dict:
    """product_id -> name / manufacturer for the table header (product_id = catalog id)."""
    comp_name = {c["id"]: c["name"] for c in cat["competitors"]}
    names = {n["id"]: {"name": n["label"], "manufacturer": "Danfoss"}
             for n in walk(cat["tree"]) if n.get("type") == "product"}
    for p in cat.get("competitorProducts", []):
        names[p["id"]] = {"name": p["name"], "manufacturer": comp_name.get(p["competitorId"], ""),
                          "equivalentOf": p.get("equivalentOf", [])}
    return names


def doc_url(source_file: str, page):
    """Where the user can open a source at the cited page:
    1. the manufacturer's own document online (data/documents.csv, filled by the datasheet crawler)
    2. otherwise the uploaded copy, if it is still stored (/files/<owner>/<fileId>_<name>#page=N)"""
    online = document_link(source_file, page)
    if online:
        return online
    for path in FILES_DIR.glob(f"*/*_{source_file}"):
        return f"/files/{path.parent.name}/{path.name}" + (f"#page={page}" if page else "")
    return None


def brief_text(payload: dict) -> tuple[str, str]:
    """The website's brief -> (question, customer context) for the AI."""
    buyer = payload.get("buyer") or {}
    factors = payload.get("factors") or {}
    must = [k for k, v in factors.items() if v == "must"]
    nice = [k for k, v in factors.items() if v == "nice"]
    context = "; ".join(x for x in [
        f"customer: {buyer.get('company')}" if buyer.get("company") else "",
        f"application: {buyer.get('application')}" if buyer.get("application") else "",
        f"uses today: {buyer.get('currentSupplier')}" if buyer.get("currentSupplier") else "",
        f"notes: {buyer.get('notes')}" if buyer.get("notes") else "",
        f"must-have topics: {', '.join(must)}" if must else "",
        f"nice-to-have topics: {', '.join(nice)}" if nice else "",
        f"own factors: {', '.join(payload.get('customFactors') or [])}" if payload.get("customFactors") else "",
    ] if x)
    question = " ".join(payload.get("chatNotes") or []) or ""
    return question, context


def ancestors(cat, node_id) -> set[str]:
    """All groups above a product: pvg32 -> {"proportional", "valves"}."""
    out, parent = set(), find_parent(cat, node_id)
    while parent:
        out.add(parent["id"])
        parent = find_parent(cat, parent["id"])
    return out


def empty_response(title, anchor, flags):
    return {"title": title, "isSample": False, "anchor": anchor, "matches": [], "attributes": [],
            "insights": {}, "sources": [], "flags": flags,
            "confidence": {"level": "low", "reason": "No extracted facts to compare yet."}}


def customer_passages(payload: dict, question: str, context: str):
    """The buyer's own feedback files (if any were uploaded), as citable facts C1, C2 ..."""
    words = re.findall(r"[a-zA-Z]{3,}", " ".join([context, question] + list((payload.get("factors") or {}).keys())))
    return customer_facts((payload.get("buyer") or {}).get("company", ""), words)


# =====================================================================
# Users and roles (demo). Design from the main branch's review queue (hrub98), scoped here to
# commercial notes: viewers SUGGEST notes, editors APPROVE them; only approved notes reach the AI.
# Swap for company single sign-on later - the rest of the code only uses current_user().
USERS = {
    "junior": {"id": "junior", "name": "Junior (demo)", "title": "Sales assistant", "role": "viewer"},
    "senior": {"id": "senior", "name": "Senior (demo)", "title": "Product manager", "role": "editor"},
}


def current_user(request: Request) -> dict:
    return USERS.get((request.headers.get("x-user") or "").strip().lower(), USERS["senior"])


@app.get("/api/user")
def get_user(request: Request):
    return {**current_user(request), "users": list(USERS.values())}


@app.get("/api/commercial")
def list_commercial(product: str = "", family: str = ""):
    """Approved + pending commercial notes for a product and its family (for the result block)."""
    ids = [product] if product else []
    return {"approved": commercial.for_products(ids, family),
            "pending": commercial.for_products(ids, family, status="pending")}


@app.post("/api/commercial")
def add_commercial(payload: dict, request: Request):
    user = current_user(request)
    try:
        row = commercial.add(payload.get("productId", ""), payload.get("kind", ""), payload.get("text", ""),
                             user, payload.get("source", ""), payload.get("page", ""), payload.get("regions", ""))
    except ValueError as e:
        return {"note": None, "issues": [issue("error", str(e))]}
    what = ("Approved and live (you are an editor)." if row["status"] == "approved"
            else "Sent for review: an editor must approve it before it is shown or used.")
    return {"note": row, "issues": [issue("ok", what)]}


@app.post("/api/commercial/decide")
def decide_commercial(payload: dict, request: Request):
    user = current_user(request)
    try:
        row = commercial.decide(payload.get("id", ""), payload.get("action", ""), user)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"note": row}


def run_engine(target_id: str, candidates, pool: str, payload: dict, names: dict, pending: dict,
               extra_flags: list) -> dict:
    question, context = brief_text(payload)
    customer = customer_passages(payload, question, context)
    with usage.collect() as calls:
        result = interpret(target_id, candidates, question, context, ask=ENGINE_ASK, use_ai=True, pool=pool,
                           customer_facts=customer)
    for pid, info in names.items():                     # confirmed equivalent pairs from the uploads
        info["knownEquivalent"] = target_id in info.get("equivalentOf", []) or pid in names.get(
            target_id, {}).get("equivalentOf", [])
    out = to_website(result, names=names, doc_url=doc_url, pending=pending, extra_flags=extra_flags)
    out["aiCost"] = ai_cost(calls, result)
    out["commercialPending"] = commercial.for_products([target_id], result.target.get("family", ""),
                                                       status="pending")
    return out


def ai_cost(calls: list, result) -> dict:
    """What this answer cost: shown under the result, so the price per comparison is visible in the demo."""
    t = usage.total(calls)
    cached = bool(result.meta.get("cached"))
    if cached:
        reason = "already compared - the stored answer was reused"
    elif not t["calls"]:                                  # why no AI: nothing to compare, no key, API error ...
        msg = result.message or ""
        reason = (msg.split("Reason:", 1)[1].strip().rstrip(".") if "Reason:" in msg else msg.rstrip(".")) or "not needed"
    else:
        reason = ""
    return {**t, "cached": cached, "reason": reason, "factsSent": result.meta.get("facts_sent")}


@app.get("/api/usage")
def get_usage():
    """Total AI spend so far (data/usage.csv), per purpose: compare, extract, ask."""
    return usage.summary()


# =====================================================================
# POST /api/compare   (website flow: Danfoss product -> competitor products)
# =====================================================================
@app.post("/api/compare")
def compare(payload: dict):
    cat = load_catalog()
    anchor = find_node(cat, payload.get("productId"))
    if not anchor:
        return empty_response("Unknown product", {"key": "x", "name": "?", "manufacturer": "Danfoss"},
                              [issue("error", "Product not found in catalog.")])
    names = catalog_names(cat)
    have = products_with_facts()
    groups = ancestors(cat, anchor["id"])         # e.g. {"proportional", "valves"}
    in_category = [p for p in cat.get("competitorProducts", []) if p["categoryId"] in groups]

    chosen_ids = payload.get("competitorProductIds") or []
    if not chosen_ids and payload.get("competitors"):
        chosen_ids = [p["id"] for p in in_category if p["competitorId"] in payload["competitors"]]
    chosen = [p for p in cat.get("competitorProducts", []) if p["id"] in chosen_ids]
    auto = not chosen_ids
    if auto:                                           # let the engine pick from the facts CSVs
        candidates = None
        pending = {p["id"]: WAITING for p in in_category if p.get("datasets") and p["id"] not in have}
    else:
        candidates = [p["id"] for p in chosen if p["id"] in have]
        pending = {p["id"]: (WAITING if p.get("datasets") else "No datasheet uploaded yet.")
                   for p in chosen if p["id"] not in have}

    flags = []
    if auto:
        flags.append(issue("unverified", "Competitor products were selected automatically from the facts "
                                         "database. Confirm functional equivalence before showing them to the customer."))
    if anchor["id"] not in have:
        flags.insert(0, issue("pending" if anchor.get("datasets") else "missing",
                              f"No extracted facts for {anchor['label']} yet. "
                              + ("Its datasheet is uploaded; the PDF→CSV step has not produced its facts CSV."
                                 if anchor.get("datasets") else "Upload its datasheet via 'Upload data'.")))
        return empty_response(f"{anchor['label']} vs. competitors",
                              {"key": anchor["id"], "name": anchor["label"], "manufacturer": "Danfoss"},
                              flags + [issue("pending", f"{names.get(p, {}).get('name', p)}: {n}") for p, n in pending.items()])
    if candidates == [] and not pending:
        return empty_response(f"{anchor['label']} vs. competitors",
                              {"key": anchor["id"], "name": anchor["label"], "manufacturer": "Danfoss"},
                              [issue("missing", "None of the chosen competitor products has data yet.")])
    return run_engine(anchor["id"], candidates, "competitors", payload, names, pending, flags)


# =====================================================================
# POST /api/extract   (PDF -> facts with the teammates' converter; one AI call per page, so only on a click)
# Request: {"ownerId": "pvg32", "fileId": "abc123...", "pages": "1-20"}   (pages optional: empty = all)
# Response: {"jobId": "..."}; progress at GET /api/jobs/<jobId>. One extraction runs at a time.
# =====================================================================
import contextlib     # noqa: E402
import tempfile       # noqa: E402
import threading      # noqa: E402
import uuid           # noqa: E402

from comparison_engine import bridge                     # noqa: E402
from comparison_engine.llm import has_api_key            # noqa: E402

JOBS: dict[str, dict] = {}
EXTRACT_LOCK = threading.Lock()
EXTRACT_CLIENT = None      # tests put a fake Anthropic client here
CONVERTER = None           # tests put a fake converter here; None = the teammates' pdf_to_csv_test.py


def find_dataset(cat, owner_id: str, file_id: str):
    """-> (owner dict, dataset dict, owner name, brand, family) or None."""
    comp_name = {c["id"]: c["name"] for c in cat["competitors"]}
    owners = [(n, n["label"], "Danfoss") for n in walk(cat["tree"]) if n.get("type") == "product"]
    owners += [(p, p["name"], comp_name.get(p["competitorId"], "")) for p in cat.get("competitorProducts", [])]
    for owner, name, brand in owners:
        if owner["id"] != owner_id:
            continue
        ds = next((d for d in owner.get("datasets", []) if d.get("fileId") == file_id), None)
        if not ds:
            return None
        start = owner["categoryId"] if "categoryId" in owner else owner["id"]
        groups = [start] + sorted(ancestors(cat, start), key=lambda g: len(ancestors(cat, g)))
        family = next((g for g in groups if not find_parent(cat, g)), start)     # top level, e.g. "valves"
        return owner, ds, name, brand, family
    return None


class _JobLog:
    """Captures the converter's printed progress ('page 3: asking the AI ...') into the job."""
    def __init__(self, job):
        self.job = job

    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                self.job["log"].append(line.strip())
                self.job["progress"] = line.strip()
        return len(text)

    def flush(self):
        pass


def run_extraction(job: dict) -> None:
    with EXTRACT_LOCK:
        job["status"] = "running"
        try:
            cat = load_catalog()
            owner, ds, name, brand, family = find_dataset(cat, job["ownerId"], job["fileId"])
            stored = FILES_DIR / job["ownerId"] / f"{ds['fileId']}_{ds['name']}"
            if ds["name"].lower().endswith(".txt"):             # web page text from the web import
                with contextlib.redirect_stdout(_JobLog(job)):
                    report = bridge.extract_text(stored.read_text(encoding="utf-8", errors="ignore"), ds["name"],
                                                 job["ownerId"], name, family, brand, ds["fileId"],
                                                 pages=job.get("pages", ""), client=EXTRACT_CLIENT,
                                                 converter=CONVERTER or bridge.load_converter())
            else:
                with tempfile.TemporaryDirectory() as tmp:      # the converter cites the file by its own name
                    pdf = Path(tmp) / ds["name"]
                    pdf.write_bytes(stored.read_bytes())
                    with contextlib.redirect_stdout(_JobLog(job)):
                        report = bridge.extract_pdf(pdf, job["ownerId"], name, family, brand, ds["fileId"],
                                                    pages=job.get("pages", ""), client=EXTRACT_CLIENT,
                                                    converter=CONVERTER or bridge.load_converter())
            # what the converter said along the way: how many pages it sent, and every page that failed
            lines = job["log"]
            sent = next((l for l in lines if "to send to the AI" in l), "")
            errors = [l.strip() for l in lines if "failed" in l.lower()]
            first_error = errors[0][:240] if errors else ""
            if not report["facts"]:                            # nothing saved: say WHY, keep it in the list
                why = (f"{len(errors)} page(s) failed, e.g. {first_error}" if errors else
                       f"the AI found no values ({sent.strip() or 'no pages with text'}). "
                       "If the PDF is a scan (pictures of text) it can't be read.")
                job.update(status="failed", report=report, progress=f"No facts extracted: {why}")
                return
            cat = load_catalog()                               # re-read: the catalog may have changed meanwhile
            found = find_dataset(cat, job["ownerId"], job["fileId"])
            if found:
                found[1]["facts"] = {"status": "done", "csv": report["csv"], "facts": report["facts"],
                                     "ok": report["ok"], "unverified": report["unverified"],
                                     "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                save_catalog(cat)
            notes = []
            if not report.get("owner_facts"):
                notes.append(f"none are filed under {name} - the file names other products: "
                             f"{', '.join(report.get('other_products', [])[:4]) or '?'}")
            elif report.get("owner_products") and name not in report["owner_products"]:
                notes.append(f"filed under {name} (the file calls it {', '.join(report['owner_products'][:2])})")
            if errors:
                notes.append(f"{len(errors)} page(s) failed, e.g. {first_error}")
            job.update(status="done", report=report,
                       progress=f"{report['facts']} facts saved ({report['ok']} verified by quote, "
                                f"{report['unverified']} unverified, {report['skipped_ordering_code']} ordering-code rows skipped)"
                                + (f" · AI cost ${report['cost']['usd']:.3f}" if report.get("cost") else "")
                                + "".join(f" · {n}" for n in notes))
        except Exception as e:  # noqa: BLE001 - shown to the user, never crashes the server
            job.update(status="failed", progress=f"Extraction failed: {e}")


@app.post("/api/extract")
def start_extraction(payload: dict):
    cat = load_catalog()
    owner_id, file_id = payload.get("ownerId"), payload.get("fileId")
    found = find_dataset(cat, owner_id, file_id)
    if not found:
        return {"jobId": None, "issues": [issue("error", "Dataset not found.")]}
    if not found[1]["name"].lower().endswith((".pdf", ".txt")):
        return {"jobId": None, "issues": [issue("error", "Only PDF datasheets and imported web pages (.txt) "
                                                         "can be converted to facts so far.")]}
    if EXTRACT_CLIENT is None and not has_api_key():
        return {"jobId": None, "issues": [issue("error", "No ANTHROPIC_API_KEY in .env - the converter needs the AI.")]}
    pages = str(payload.get("pages") or "").strip()
    if pages and not re.fullmatch(r"[\d,\-\s]+", pages):
        return {"jobId": None, "issues": [issue("error", "Pages must look like 1,3-5,8.")]}
    job = {"id": uuid.uuid4().hex[:12], "ownerId": owner_id, "fileId": file_id, "name": found[1]["name"],
           "pages": pages, "status": "queued", "progress": "Waiting for the previous extraction to finish…"
           if EXTRACT_LOCK.locked() else "Starting…", "log": []}
    JOBS[job["id"]] = job
    threading.Thread(target=run_extraction, args=(job,), daemon=True).start()
    return {"jobId": job["id"], "issues": [issue("pending", f"Extracting facts from {job['name']} - this takes "
                                                              "a few seconds per page.")]}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return {"status": "unknown", "progress": "Job not found (the server may have restarted)."}
    return {k: v for k, v in job.items() if k != "log"} | {"log": job["log"][-8:]}


# =====================================================================
# Web import with the teammates' scraper (scraper/ in the repo root; no AI, no API key)
#   POST /api/web/discover {"url": "https://manufacturer.com/...", "topic": "valves"} -> candidate documents
#   POST /api/web/import   {"url": "<chosen document>", "source": "danfoss"|"competitor", ...same target fields as
#                           the upload...}  -> stored like an upload; its web address goes into data/documents.csv
# =====================================================================
import sys            # noqa: E402

if str(BASE.parent) not in sys.path:
    sys.path.append(str(BASE.parent))                    # the repo root, where scraper/ lives
from comparison_engine.documents import save_document     # noqa: E402

try:
    from scraper import discover as web_discover, download_source as web_download   # noqa: E402
except ImportError:                                      # scraper folder missing: the rest of the app still works
    web_discover = web_download = None


@app.post("/api/web/discover")
def discover_documents(payload: dict):
    if web_discover is None:
        return {"documents": [], "issues": [issue("error", "The scraper module is not installed (scraper/ folder).")]}
    url, topic = (payload.get("url") or "").strip(), (payload.get("topic") or "pumps").strip()
    try:
        result = web_discover(url, topic=topic)
    except Exception as e:  # invalid / private URL, site unreachable, no HTML ...
        return {"documents": [], "issues": [issue("error", f"Could not search {url}: {e}")]}
    issues = [issue("warning", w) for w in result.get("warnings", [])]
    if not result["documents"]:
        issues.append(issue("missing", "No documents found there. Try a more specific product or download page."))
    if result.get("limited"):
        issues.append(issue("warning", "Search stopped early (page limit) - a more specific URL may find more."))
    return {**result, "issues": issues}


@app.get("/api/web/open")
def open_web_document(url: str):
    """The 'open' link in the web list: resolve the entry to the real file and show it (PDF) instead of its web page."""
    from fastapi.responses import Response
    if web_download is None:
        return Response("The scraper module is not installed.", status_code=503, media_type="text/plain")
    try:
        content, filename, _source, _ = web_download(url.strip())
    except Exception as e:
        return Response(f"Could not open this document: {e}", status_code=502, media_type="text/plain")
    media = "application/pdf" if content.startswith(b"%PDF-") else "text/plain; charset=utf-8"
    return Response(content, media_type=media, headers={"Content-Disposition": f'inline; filename="{filename}"'})


@app.post("/api/web/import")
def import_from_web(payload: dict):
    if web_download is None:
        return {"uploaded": [], "issues": [issue("error", "The scraper module is not installed (scraper/ folder).")]}
    cat = load_catalog()
    found = resolve_target(cat, payload.get("source", "competitor"), payload.get("nodeId"), payload.get("competitorId"),
                           payload.get("competitorName"), payload.get("productName"), payload.get("categoryId"),
                           payload.get("equivalentOf"))
    if "error" in found:
        return {"uploaded": [], "issues": found["issues"]}
    try:
        content, filename, source_url, _ = web_download((payload.get("url") or "").strip())
    except Exception as e:
        return {"uploaded": [], "issues": [issue("error", f"Download failed: {e}")]}
    issues = found["issues"]
    ds = store_file(found, filename, content, issues)
    finish_upload(cat, found, [ds] if ds else [], issues)
    if not ds:
        return {"uploaded": [], "issues": issues}
    try:
        save_document(filename, source_url, title=payload.get("title") or filename, found_by="scraper")
        issues.append(issue("ok", f"Online link saved: footnotes for {filename} open {source_url}."))
    except ValueError as e:
        issues.append(issue("warning", f"Link not saved: {e}"))
    if not filename.lower().endswith(".pdf"):
        issues.append(issue("ok", "This is a web page, not a PDF: its text was saved and can be turned into facts "
                                  "below (each ~4000 characters count as one 'page')."))
    return {"uploaded": [filename], "issues": issues, "sourceUrl": source_url,
            "datasets": [{"ownerId": found["owner_id"], "fileId": ds["fileId"], "name": ds["name"]}]}


# =====================================================================
# GET /api/products   (everything that has facts, for the system builder)
# =====================================================================
@app.get("/api/products")
def list_products():
    data = load_repository()
    if data.empty:
        return {"products": []}
    names = catalog_names(load_catalog())
    out = []
    for pid, g in data.groupby("product_id"):
        brand = g["brand"].iloc[0]
        out.append({"id": pid, "name": names.get(pid, {}).get("name") or g["product_name"].iloc[0],
                    "brand": names.get(pid, {}).get("manufacturer") or brand, "family": g["family"].iloc[0],
                    "side": "danfoss" if brand.lower() == "danfoss" else "competitor",
                    "demo": bool("DEMO" in pid or g["source_file"].astype(str).str.contains("DEMO").any())})
    return {"products": sorted(out, key=lambda p: (p["family"], p["side"], p["name"]))}


# =====================================================================
# POST /api/compare-system   (a set of components, e.g. pump + valve, against another set)
# Request: { "start": "danfoss" | "competitor",            which side the user describes
#            "components": {"pump": "<product id>", "valve": "<product id>"},
#            "against":    {"pump": null, "valve": "<product id>"},   null = best match chosen automatically
#            "buyer": {...}, "factors": {...}, "chatNotes": [...] }
# Response: same shape as /api/compare; rows are "Pump · ...", "Valve · ..." and "System · ..." values.
# =====================================================================
@app.post("/api/compare-system")
def compare_system_endpoint(payload: dict):
    start = "competitor" if payload.get("start") == "competitor" else "danfoss"
    have = products_with_facts()
    components = {r: p for r, p in (payload.get("components") or {}).items() if p}
    names = catalog_names(load_catalog())
    side_name = {"danfoss": ("Danfoss system", "Danfoss"), "competitor": ("Competitor system", "Competitors")}
    anchor_name, anchor_brand = side_name[start]
    other_name, other_brand = side_name["competitor" if start == "danfoss" else "danfoss"]
    no_data = [p for p in components.values() if p not in have]
    if len(components) < 2 or no_data:
        why = ("Choose at least two components (e.g. a pump and a valve)." if len(components) < 2 else
               f"No extracted facts yet for: {', '.join(names.get(p, {}).get('name', p) for p in no_data)}.")
        return empty_response(f"{anchor_name} vs. {other_name}", {"key": "system:anchor", "name": anchor_name,
                                                                   "manufacturer": ""}, [issue("missing", why)])
    anchor = System("system:anchor", anchor_name, anchor_brand, components)
    fixed = {role: (payload.get("against") or {}).get(role) for role in components}
    other = compose_system("system:other", other_name, other_brand, fixed, anchor,
                           pool="competitors" if start == "danfoss" else "danfoss")
    question, context = brief_text(payload)
    with usage.collect() as calls:
        result = compare_systems(anchor, other, question, context, ask=ENGINE_ASK,
                                 customer_facts=customer_passages(payload, question, context),
                                 pool="competitors" if start == "danfoss" else "danfoss")

    def parts(system):
        return " + ".join(f"{names.get(p, {}).get('name') or p.split('|')[-1]} ({r})"
                          for r, p in system.components.items() if p) or "no components with data"
    flags = [issue("unverified", f"{other_name} · {n}") for n in other.notes if "automatically" in n]
    flags += [issue("missing", f"{other_name} · {n}") for n in other.notes if "automatically" not in n]
    out = to_website(result, names={anchor.id: {"name": anchor_name, "manufacturer": parts(anchor)},
                                    other.id: {"name": other_name, "manufacturer": parts(other)}},
                     doc_url=doc_url, extra_flags=flags)
    out["aiCost"] = ai_cost(calls, result)
    return out


# =====================================================================
# POST /api/match   (reverse flow: competitor product the customer uses -> best Danfoss products)
# Request: { "competitorProductId": "kawasaki-kmp", "buyer": {...}, "factors": {...}, "chatNotes": [...] }
# Response: same shape as /api/compare, with the competitor product as the anchor column.
# =====================================================================
@app.post("/api/match")
def match(payload: dict):
    cat = load_catalog()
    names = catalog_names(cat)
    pid = payload.get("competitorProductId")
    product = next((p for p in cat.get("competitorProducts", []) if p["id"] == pid), None)
    if not product:
        return empty_response("Unknown competitor product", {"key": "x", "name": "?", "manufacturer": ""},
                              [issue("error", "Competitor product not found in catalog.")])
    anchor = {"key": pid, "name": product["name"], "manufacturer": names[pid]["manufacturer"]}
    if pid not in products_with_facts():
        return empty_response(f"{product['name']} vs. Danfoss", anchor, [issue(
            "pending" if product.get("datasets") else "missing",
            f"No extracted facts for {product['name']} yet. "
            + ("Its datasheet is uploaded; the PDF→CSV step has not produced its facts CSV."
               if product.get("datasets") else "Upload its datasheet in the Competitors step."))])
    flags = [issue("unverified", "Danfoss products were selected automatically from the facts database. "
                                 "Confirm functional equivalence before showing them to the customer.")]
    return run_engine(pid, None, "danfoss", payload, names, {}, flags)


# =====================================================================
# POST /api/ask
# =====================================================================
DANI_INTRO = ("I'm Dani, the Danfoss competitor-intelligence assistant. I answer only from the datasheets "
              "and sales guidelines loaded into this tool, and I cite the page for every value. Pick a product, "
              "then ask me things like \"which competitor valve fits a tractor loader at 120 l/min?\"")
_DANI = re.compile(r"^\s*(hi|hey|hello|ok)?[\s,]*dani[\s,:!.-]*", re.I)


@app.post("/api/ask")
def ask(payload: dict):
    question = (payload.get("question") or "").strip()
    named = bool(_DANI.match(question))
    question = _DANI.sub("", question).strip() or question      # "Dani, what is ..." -> "what is ..."
    ctx = payload.get("context") or {}
    cat = load_catalog()
    anchor = find_node(cat, ctx.get("productId")) if ctx.get("productId") else None
    if re.fullmatch(r"(who are you|what are you|what can you do|help)\??", question.lower()) or (named and not question):
        return {"answer": DANI_INTRO}
    if not anchor:
        return {"answer": ("Hi, I'm Dani. " if named else "") +
                          "Pick a product above first. I'll use your question as context for the comparison."}

    groups = ancestors(cat, anchor["id"])
    chosen = ctx.get("competitorProductIds") or []
    comps = [p for p in cat.get("competitorProducts", []) if p.get("datasets") and (
        p["id"] in chosen if chosen else p["categoryId"] in groups
        and (not ctx.get("competitors") or p["competitorId"] in ctx["competitors"]))][:3]
    words = [w for w in re.findall(r"[a-zA-Z/°]{3,}", question.lower())]
    # token diet: only the few pages that best match the question (was 8 + 3x6 pages, up to ~70,000 characters)
    blocks = [pages_as_prompt(f"Danfoss {anchor['label']}", select_pages(pages_for(anchor.get("datasets")), words, limit=4, max_chars=12000))]
    for p in comps:
        blocks.append(pages_as_prompt(p["name"], select_pages(pages_for(p.get("datasets")), words, limit=3, max_chars=6000)))

    try:
        resp = claude().messages.create(
            model=MODEL, max_tokens=800,
            system="You are Dani, the Danfoss competitor-intelligence assistant for sales teams. "
                   "If asked who you are: you are Dani; you answer only from the loaded documents and cite pages.\n"
                   + RULES +
                   "\nAnswer briefly in plain text (no markdown). Cite as (document, p. N). "
                   "If the pages don't contain the answer, say exactly what is missing and which datasheet "
                   "to upload - never fill the gap from memory. No prices or delivery promises: commercial "
                   "statements only come from approved notes in the comparison itself. If the question is "
                   "not about these products or this tool, say in one line that you only cover the loaded "
                   "product data.",
            messages=[{"role": "user", "content": "\n".join(blocks) + f"\n\nQuestion: {question}"}],
        )
        return {"answer": "".join(b.text for b in resp.content if b.type == "text")}
    except Exception as e:
        return {"answer": f"Sorry, the assistant failed: {e}"}


# =====================================================================
# Serve the front end on the same address (mount LAST so /api wins)
# =====================================================================
# Stored uploads, so every source link opens the original file at the cited page
app.mount("/files", StaticFiles(directory=FILES_DIR), name="files")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
