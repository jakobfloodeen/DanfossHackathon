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
from fastapi import FastAPI, File, Form, UploadFile
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

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
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
    files: list[UploadFile] = File(...),
    nodeId: Optional[str] = Form(None),
    competitorId: Optional[str] = Form(None),
    competitorName: Optional[str] = Form(None),
    productName: Optional[str] = Form(None),
    categoryId: Optional[str] = Form(None),
    equivalentOf: Optional[str] = Form(None),
    customerName: Optional[str] = Form(None),
):
    if source == "customer":                     # customer feedback: only the extracted text is kept (as CSV)
        return await upload_customer_files(customerName, files)
    cat = load_catalog()
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
            return {"uploaded": [], "issues": [issue("error", "Choose or name a competitor.")]}
        pname = (productName or "").strip()
        if not pname:
            return {"uploaded": [], "issues": [issue("error", "Name the competitor product or series.")]}
        category = find_node(cat, categoryId)
        if not category or category.get("type") == "product":
            return {"uploaded": [], "issues": [issue("error", "Choose the product category it competes in.")]}

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
            return {"uploaded": [], "issues": [issue("error", "Target product or group not found.")]}
        label, owner_id, comp = target["label"], target["id"], None

    target.setdefault("datasets", [])
    uploaded = []
    for f in files:
        name = Path(f.filename).name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        data = await f.read()
        if ext not in ALLOWED_EXT:
            issues.append(issue("error", f"{name}: unsupported file type (.{ext}).")); continue
        if not data:
            issues.append(issue("error", f"{name}: file is empty.")); continue
        if len(data) > MAX_MB * 1024 * 1024:
            issues.append(issue("error", f"{name}: larger than {MAX_MB} MB.")); continue

        file_id = hashlib.sha256(data).hexdigest()[:16]
        folder = FILES_DIR / owner_id
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

        target["datasets"] = [d for d in target["datasets"] if d["name"] != name]
        target["datasets"].append({
            "name": name, "fileId": file_id, "source": source,
            "competitorId": comp["id"] if comp else None,
            "pages": len(pages), "uploadedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        uploaded.append(name)

    if uploaded:
        target["status"] = "active"
        if source == "danfoss" and target.get("desc", "").startswith("No datasheet"):
            target["desc"] = f"{len(target['datasets'])} dataset(s) loaded"
        if comp:
            comp["hasData"] = True
        issues.append(issue("ok", f"{len(uploaded)} file(s) for {label} stored and text extracted. "
                                  "Values are extracted when you generate a comparison."))
    save_catalog(cat)
    return {"uploaded": uploaded, "issues": issues}


# =====================================================================
# Claude helpers
# =====================================================================
def claude():
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic()




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


def run_engine(target_id: str, candidates, pool: str, payload: dict, names: dict, pending: dict,
               extra_flags: list) -> dict:
    question, context = brief_text(payload)
    customer = customer_passages(payload, question, context)
    result = interpret(target_id, candidates, question, context, ask=ENGINE_ASK, use_ai=True, pool=pool,
                       customer_facts=customer)
    for pid, info in names.items():                     # confirmed equivalent pairs from the uploads
        info["knownEquivalent"] = target_id in info.get("equivalentOf", []) or pid in names.get(
            target_id, {}).get("equivalentOf", [])
    return to_website(result, names=names, doc_url=doc_url, pending=pending, extra_flags=extra_flags)


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
    result = compare_systems(anchor, other, question, context, ask=ENGINE_ASK,
                             customer_facts=customer_passages(payload, question, context),
                             pool="competitors" if start == "danfoss" else "danfoss")

    def parts(system):
        return " + ".join(f"{names.get(p, {}).get('name') or p.split('|')[-1]} ({r})"
                          for r, p in system.components.items() if p) or "no components with data"
    flags = [issue("unverified", f"{other_name} · {n}") for n in other.notes if "automatically" in n]
    flags += [issue("missing", f"{other_name} · {n}") for n in other.notes if "automatically" not in n]
    return to_website(result, names={anchor.id: {"name": anchor_name, "manufacturer": parts(anchor)},
                                     other.id: {"name": other_name, "manufacturer": parts(other)}},
                      doc_url=doc_url, extra_flags=flags)


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
@app.post("/api/ask")
def ask(payload: dict):
    question = (payload.get("question") or "").strip()
    ctx = payload.get("context") or {}
    cat = load_catalog()
    anchor = find_node(cat, ctx.get("productId")) if ctx.get("productId") else None
    if not anchor:
        return {"answer": "Pick a product above first. I'll use your question as context for the comparison."}

    groups = ancestors(cat, anchor["id"])
    chosen = ctx.get("competitorProductIds") or []
    comps = [p for p in cat.get("competitorProducts", []) if p.get("datasets") and (
        p["id"] in chosen if chosen else p["categoryId"] in groups
        and (not ctx.get("competitors") or p["competitorId"] in ctx["competitors"]))][:3]
    words = [w for w in re.findall(r"[a-zA-Z/°]{3,}", question.lower())]
    blocks = [pages_as_prompt(f"Danfoss {anchor['label']}", select_pages(pages_for(anchor.get("datasets")), words, limit=8, max_chars=25000))]
    for p in comps:
        blocks.append(pages_as_prompt(p["name"], select_pages(pages_for(p.get("datasets")), words, limit=6, max_chars=15000)))

    try:
        resp = claude().messages.create(
            model=MODEL, max_tokens=800,
            system=RULES + "\nAnswer briefly in plain text (no markdown). Cite as (document, p. N). "
                           "If the pages don't contain the answer, say so.",
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
