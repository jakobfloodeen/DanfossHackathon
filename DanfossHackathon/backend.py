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
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
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
# Front end and API are served from the same address, so cookies work without extra CORS setup.
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
# Users and roles (demo)
#
# Two roles:
#   viewer  read everything, run comparisons, REQUEST changes (review queue)
#   editor  everything a viewer can, plus change the catalog directly and
#           approve / reject requests
#
# DEMO ONLY: the account is picked with a switch in the top bar and kept in a
# cookie, with no password. In production, replace current_user() with the
# company single sign-on (Microsoft Entra ID) and map its groups to these roles.
# The rest of the code only uses current_user() / require_editor().
# =====================================================================
USERS = {
    "junior": {"id": "junior", "name": "Junior (demo)", "title": "Sales assistant", "role": "viewer"},
    "senior": {"id": "senior", "name": "Senior (demo)", "title": "Product manager", "role": "editor"},
}
DEFAULT_USER = "junior"          # unknown or missing cookie = least access
SESSION_COOKIE = "ci_user"


def current_user(request: Request) -> dict:
    return USERS.get(request.cookies.get(SESSION_COOKIE, ""), USERS[DEFAULT_USER])


def require_editor(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "editor":
        raise HTTPException(status_code=403,
                            detail="Only editors can change the catalog. Use 'Request upload' to send it for review.")
    return user


class SessionIn(BaseModel):
    userId: str


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    pending = [s for s in load_suggestions() if s["status"] == "pending"]
    mine = [s for s in load_suggestions() if s["submittedBy"]["id"] == user["id"]]
    return {"user": user, "users": list(USERS.values()),
            "pendingReviews": len(pending) if user["role"] == "editor" else 0,
            "myOpenRequests": sum(1 for s in mine if s["status"] == "pending")}


@app.post("/api/session")
def switch_user(body: SessionIn, response: Response):
    if body.userId not in USERS:
        raise HTTPException(status_code=404, detail="Unknown demo account.")
    response.set_cookie(SESSION_COOKIE, body.userId, httponly=True, samesite="lax")
    return {"user": USERS[body.userId]}


def who(user: dict) -> dict:
    return {"id": user["id"], "name": user["name"]}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =====================================================================
# GET /api/catalog
# =====================================================================
@app.get("/api/catalog")
def get_catalog():
    return load_catalog()


# =====================================================================
# Shared helpers: used by direct edits AND by approved requests,
# so both paths validate and store data in exactly the same way.
# =====================================================================
def check_file(name: str, data: bytes) -> Optional[dict]:
    """Return an error issue if the file can't be accepted, else None."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        return issue("error", f"{name}: unsupported file type (.{ext}).")
    if not data:
        return issue("error", f"{name}: file is empty.")
    if len(data) > MAX_MB * 1024 * 1024:
        return issue("error", f"{name}: larger than {MAX_MB} MB.")
    return None


def apply_create_node(cat: dict, parent_id, label: str, desc: str, node_type: str, user: dict):
    """Validate and add a family / type / product. Returns (node or None, issues)."""
    parent = find_node(cat, parent_id) if parent_id else None
    if parent_id and not parent:
        return None, [issue("error", "Parent group not found.")]
    if parent and parent.get("type") == "product":
        return None, [issue("error", "A product cannot contain other items.")]
    name = (label or "").strip()
    if not name:
        return None, [issue("error", "Name is required.")]
    siblings = parent.setdefault("children", []) if parent else cat["tree"]
    if any(n["label"].lower() == name.lower() for n in siblings):
        return None, [issue("conflict", f'"{name}" already exists at this level.')]

    node_id = slug(name)
    while find_node(cat, node_id):
        node_id += "-2"
    node = {"id": node_id, "label": name, "desc": (desc or "").strip(), "status": "data-pending",
            "datasets": [], "addedBy": who(user), "addedAt": now_iso()}
    if node_type == "product":
        node["type"] = "product"
    else:
        node.update(prompt="Choose one:", children=[])
    if parent and parent.get("status") == "coming-soon":
        parent["status"] = "active"
    siblings.append(node)

    issues = [issue("ok", f'Added "{name}".')]
    if not node["desc"]:
        issues.append(issue("warning", "No description given; add one so users can tell items apart."))
    return node, issues


def apply_datasets(cat: dict, meta: dict, files: list, user: dict, approved_by: Optional[dict] = None):
    """Store files on a Danfoss node or a competitor product and extract their text.
    files = [(filename, bytes)]. Returns (uploaded names, issues)."""
    issues = []
    source = meta.get("source")
    if source == "competitor":
        comp = next((c for c in cat["competitors"] if c["id"] == meta.get("competitorId")), None)
        new_name = (meta.get("competitorName") or "").strip()
        if not comp and new_name:
            comp = next((c for c in cat["competitors"] if c["name"].lower() == new_name.lower()), None)
            if comp:
                issues.append(issue("warning", f'"{new_name}" already exists as a competitor; files attached to it.'))
            else:
                comp = {"id": slug(new_name), "name": new_name, "hasData": False}
                cat["competitors"].append(comp)
                issues.append(issue("ok", f'Added competitor "{new_name}".'))
        if not comp:
            return [], [issue("error", "Choose or name a competitor.")]
        pname = (meta.get("productName") or "").strip()
        if not pname:
            return [], [issue("error", "Name the competitor product or series.")]
        category = find_node(cat, meta.get("categoryId"))
        if not category or category.get("type") == "product":
            return [], [issue("error", "Choose the product category it competes in.")]

        target = next((p for p in cat["competitorProducts"]
                       if p["competitorId"] == comp["id"] and p["name"].lower() == pname.lower()), None)
        if target:
            issues.append(issue("warning", f"{comp['name']} {pname} already exists; files added to it."))
        else:
            target = {"id": f"{comp['id']}-{slug(pname)}", "competitorId": comp["id"], "name": pname,
                      "categoryId": category["id"], "equivalentOf": [], "datasets": [], "status": "pending",
                      "addedBy": who(user), "addedAt": now_iso()}
            cat["competitorProducts"].append(target)
            issues.append(issue("ok", f"Added competitor product {comp['name']} {pname} under {category['label']}."))
        eq = meta.get("equivalentOf")
        if eq and eq not in target["equivalentOf"]:
            target["equivalentOf"].append(eq)
            issues.append(issue("ok", "Saved as a confirmed equivalent pair (also used to test matching accuracy)."))
        label, owner_id = f"{comp['name']} {pname}", target["id"]
    else:
        target = find_node(cat, meta.get("nodeId"))
        if not target:
            return [], [issue("error", "Target product or group not found.")]
        label, owner_id, comp = target["label"], target["id"], None

    target.setdefault("datasets", [])
    uploaded = []
    for name, data in files:
        name = Path(name).name
        problem = check_file(name, data)
        if problem:
            issues.append(problem)
            continue
        ext = name.rsplit(".", 1)[-1].lower()
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

        entry = {"name": name, "fileId": file_id, "source": source,
                 "competitorId": comp["id"] if comp else None,
                 "pages": len(pages), "uploadedAt": now_iso(), "addedBy": who(user)}
        if approved_by:
            entry["approvedBy"] = who(approved_by)
        target["datasets"] = [d for d in target["datasets"] if d["name"] != name]
        target["datasets"].append(entry)
        uploaded.append(name)

    if uploaded:
        target["status"] = "active"
        if source == "danfoss" and target.get("desc", "").startswith("No datasheet"):
            target["desc"] = f"{len(target['datasets'])} dataset(s) loaded"
        if comp:
            comp["hasData"] = True
        issues.append(issue("ok", f"{len(uploaded)} file(s) for {label} stored and text extracted. "
                                  "Values are extracted when you generate a comparison."))
    return uploaded, issues


# =====================================================================
# POST /api/catalog/nodes   (editor only)
# =====================================================================
class NodeIn(BaseModel):
    parentId: Optional[str] = None
    label: str = ""
    desc: str = ""
    type: str = "group"


@app.post("/api/catalog/nodes")
def create_node(body: NodeIn, user: dict = Depends(require_editor)):
    cat = load_catalog()
    node, issues = apply_create_node(cat, body.parentId, body.label, body.desc, body.type, user)
    if node:
        save_catalog(cat)
    return {"node": node, "issues": issues}


# =====================================================================
# POST /api/datasets  (multipart upload, editor only)
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
    user: dict = Depends(require_editor),
):
    meta = {"source": source, "nodeId": nodeId, "competitorId": competitorId, "competitorName": competitorName,
            "productName": productName, "categoryId": categoryId, "equivalentOf": equivalentOf}
    data = [(f.filename, await f.read()) for f in files]
    cat = load_catalog()
    uploaded, issues = apply_datasets(cat, meta, data, user)
    save_catalog(cat)
    return {"uploaded": uploaded, "issues": issues}


# =====================================================================
# Review queue: viewers request changes, editors approve or reject.
# Requests are stored in data/suggestions.json, their files in
# data/pending/<id>/. Nothing reaches the catalog (or comparisons)
# until an editor approves it.
# =====================================================================
SUGGESTIONS_FILE = DATA_DIR / "suggestions.json"
PENDING_DIR = DATA_DIR / "pending"


def load_suggestions() -> list:
    if not SUGGESTIONS_FILE.exists():
        return []
    return json.loads(SUGGESTIONS_FILE.read_text(encoding="utf-8"))


def save_suggestions(items: list) -> None:
    SUGGESTIONS_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def describe_request(cat: dict, s: dict) -> str:
    """One readable line for the review queue."""
    m = s["meta"]
    if s["kind"] == "node":
        parent = find_node(cat, m.get("parentId"))
        what = "product" if m.get("type") == "product" else "group"
        return f'New {what} "{m.get("label")}"' + (f' in {parent["label"]}' if parent else " (new product family)")
    if m.get("source") == "competitor":
        comp = next((c["name"] for c in cat["competitors"] if c["id"] == m.get("competitorId")), None) \
            or m.get("competitorName") or "?"
        category = find_node(cat, m.get("categoryId"))
        eq = find_node(cat, m.get("equivalentOf")) if m.get("equivalentOf") else None
        return (f"Competitor datasheet: {comp} {m.get('productName')}"
                + (f" in {category['label']}" if category else "")
                + (f" (equivalent of {eq['label']})" if eq else ""))
    node = find_node(cat, m.get("nodeId"))
    return f"Danfoss datasheet for {node['label'] if node else m.get('nodeId')}"


@app.post("/api/suggestions")
async def create_suggestion(
    kind: str = Form(...),                       # "node" | "dataset"
    files: list[UploadFile] = File(default=[]),
    note: str = Form(""),
    parentId: Optional[str] = Form(None),
    label: Optional[str] = Form(None),
    desc: Optional[str] = Form(None),
    type: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    nodeId: Optional[str] = Form(None),
    competitorId: Optional[str] = Form(None),
    competitorName: Optional[str] = Form(None),
    productName: Optional[str] = Form(None),
    categoryId: Optional[str] = Form(None),
    equivalentOf: Optional[str] = Form(None),
    user: dict = Depends(current_user),
):
    cat = load_catalog()
    issues = []
    if kind == "node":
        meta = {"parentId": parentId or None, "label": (label or "").strip(), "desc": (desc or "").strip(),
                "type": type or "group"}
        # Same checks the editor would hit, so obvious problems surface before review.
        _, check = apply_create_node(json.loads(json.dumps(cat)), meta["parentId"], meta["label"],
                                     meta["desc"], meta["type"], user)
        if any(i["level"] in ("error", "conflict") for i in check):
            return {"suggestion": None, "issues": check}
    elif kind == "dataset":
        meta = {"source": source, "nodeId": nodeId, "competitorId": competitorId,
                "competitorName": competitorName, "productName": productName,
                "categoryId": categoryId, "equivalentOf": equivalentOf}
        if not files:
            return {"suggestion": None, "issues": [issue("error", "No files selected.")]}
    else:
        return {"suggestion": None, "issues": [issue("error", "Unknown request type.")]}

    sid = hashlib.sha256(f"{user['id']}{now_iso()}{os.urandom(8).hex()}".encode()).hexdigest()[:12]
    folder = PENDING_DIR / sid
    stored = []
    for f in files or []:
        name = Path(f.filename).name
        data = await f.read()
        problem = check_file(name, data)
        if problem:
            issues.append(problem)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        stored.append({"name": name, "size": len(data)})
    if kind == "dataset" and not stored:
        return {"suggestion": None, "issues": issues or [issue("error", "No valid files.")]}

    s = {"id": sid, "kind": kind, "meta": meta, "files": stored, "note": note.strip(),
         "submittedBy": who(user), "submittedAt": now_iso(), "status": "pending"}
    s["summary"] = describe_request(cat, s)
    items = load_suggestions()
    items.append(s)
    save_suggestions(items)
    issues.append(issue("pending", f"Sent for review: {s['summary']}. An editor has to approve it "
                                   "before it is used in comparisons."))
    return {"suggestion": s, "issues": issues}


@app.get("/api/suggestions")
def list_suggestions(user: dict = Depends(current_user)):
    items = load_suggestions()
    if user["role"] != "editor":                       # viewers only see their own requests
        items = [s for s in items if s["submittedBy"]["id"] == user["id"]]
    return sorted(items, key=lambda s: (s["status"] != "pending", s["submittedAt"]), reverse=False)


@app.get("/api/suggestions/{sid}/files/{name}")
def suggestion_file(sid: str, name: str, user: dict = Depends(require_editor)):
    path = (PENDING_DIR / sid / Path(name).name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path)


class RejectIn(BaseModel):
    reason: str = ""


def _get_pending(sid: str):
    items = load_suggestions()
    s = next((x for x in items if x["id"] == sid), None)
    if not s:
        raise HTTPException(status_code=404, detail="Request not found.")
    if s["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"This request was already {s['status']}.")
    return items, s


@app.post("/api/suggestions/{sid}/approve")
def approve_suggestion(sid: str, user: dict = Depends(require_editor)):
    items, s = _get_pending(sid)
    cat = load_catalog()
    files = [(f["name"], (PENDING_DIR / sid / f["name"]).read_bytes()) for f in s["files"]]
    m = s["meta"]
    submitter = s["submittedBy"] | {"role": "viewer"}

    if s["kind"] == "node":
        node, issues = apply_create_node(cat, m.get("parentId"), m.get("label"), m.get("desc"), m.get("type"),
                                         submitter)
        if not node:  # e.g. someone added the same name meanwhile
            return {"suggestion": s, "issues": issues}
        node["approvedBy"] = who(user)
        if files:
            _, more = apply_datasets(cat, {"source": "danfoss", "nodeId": node["id"]}, files, submitter, user)
            issues += more
    else:
        uploaded, issues = apply_datasets(cat, m, files, submitter, user)
        if not uploaded:
            return {"suggestion": s, "issues": issues}

    save_catalog(cat)
    s.update(status="approved", reviewedBy=who(user), reviewedAt=now_iso())
    save_suggestions(items)
    shutil.rmtree(PENDING_DIR / sid, ignore_errors=True)
    return {"suggestion": s, "issues": [issue("ok", f"Approved: {s['summary']}.")] + issues}


@app.post("/api/suggestions/{sid}/reject")
def reject_suggestion(sid: str, body: RejectIn, user: dict = Depends(require_editor)):
    items, s = _get_pending(sid)
    s.update(status="rejected", reviewedBy=who(user), reviewedAt=now_iso(), reason=body.reason.strip())
    save_suggestions(items)
    shutil.rmtree(PENDING_DIR / sid, ignore_errors=True)
    return {"suggestion": s, "issues": [issue("ok", f"Rejected: {s['summary']}.")]}


# =====================================================================
# Claude helpers
# =====================================================================
def claude():
    import anthropic
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env")
    return anthropic.Anthropic()


COMPARE_TOOL = {
    "name": "submit_comparison",
    "description": "Return the structured, source-linked comparison.",
    "input_schema": {
        "type": "object",
        "properties": {
            "attributes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "factor": {"type": "string", "description": "One of the factor ids, or 'custom'"},
                        "label": {"type": "string"},
                        "unit": {"type": "string"},
                        "values": {
                            "type": "object",
                            "description": "Keyed by product key. Each: {value: string|null, status, note?, sources: [{file_id, page}]}",
                        },
                    },
                    "required": ["id", "factor", "label", "unit", "values"],
                },
            },
            "insights": {
                "type": "object",
                "description": "{differences:[{text,sources:[{file_id,page}]}], benefits:[...], tradeoffs:[...]}",
            },
            "flags": {"type": "array", "items": {"type": "object"}, "description": "[{level, text}]"},
            "confidence": {"type": "object", "description": "{level: high|medium|low, reason}"},
        },
        "required": ["attributes", "insights", "flags", "confidence"],
    },
}

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


def build_sources(result: dict, valid: dict) -> list:
    """Turn {file_id, page} citations into the numbered sources[] list the UI expects.
    Citations that don't match a page we actually sent are dropped and the value is flagged."""
    numbered, sources = {}, []
    dropped = 0

    def ref(cits):
        nonlocal dropped
        out = []
        for c in cits or []:
            if not isinstance(c, dict):
                continue
            try:
                key = (str(c.get("file_id")), int(c.get("page", 0) or 0))
            except (TypeError, ValueError):
                key = None
            if key not in valid:
                dropped += 1
                continue
            if key not in numbered:
                numbered[key] = len(sources) + 1
                doc = valid[key]
                sources.append({"id": numbered[key], "title": doc["title"], "doc": doc["doc"],
                                "page": str(key[1]), "date": doc.get("date", "")})
            if numbered[key] not in out:
                out.append(numbered[key])
        return out

    for a in result.get("attributes", []):
        for v in (a.get("values") or {}).values():
            if isinstance(v, dict):
                v["sources"] = ref(v.get("sources"))
                if v.get("value") is not None and not v["sources"] and v.get("status") in (None, "ok"):
                    v["status"] = "unverified"
    for group in ("differences", "benefits", "tradeoffs"):
        for it in (result.get("insights") or {}).get(group, []) or []:
            it["sources"] = ref(it.get("sources"))
    if dropped:
        result.setdefault("flags", []).append(
            issue("unverified", f"{dropped} citation(s) pointed to pages that weren't in the provided sources and were removed."))
    return sources


def pick_competitors(cat, payload, anchor_id, category_id):
    prods = cat.get("competitorProducts", [])
    if payload.get("competitorProductIds"):
        return [p for p in prods if p["id"] in payload["competitorProductIds"]], False
    in_cat = [p for p in prods if p["categoryId"] == category_id and p.get("datasets")]
    if payload.get("competitors"):
        in_cat = [p for p in in_cat if p["competitorId"] in payload["competitors"]]
    # Confirmed equivalents first, then the rest (max 3 columns)
    in_cat.sort(key=lambda p: anchor_id not in p.get("equivalentOf", []))
    return in_cat[:3], True


# =====================================================================
# POST /api/compare
# =====================================================================
@app.post("/api/compare")
def compare(payload: dict):
    cat = load_catalog()
    anchor = find_node(cat, payload.get("productId"))
    if not anchor:
        return {"title": "Unknown product", "anchor": {"key": "x", "name": "?", "manufacturer": "Danfoss"},
                "matches": [], "attributes": [], "insights": {}, "sources": [],
                "flags": [issue("error", "Product not found in catalog.")],
                "confidence": {"level": "low", "reason": "No product."}}
    category = find_parent(cat, anchor["id"])
    comps, auto = pick_competitors(cat, payload, anchor["id"], category["id"] if category else None)
    comp_name = {c["id"]: c["name"] for c in cat["competitors"]}

    anchor_info = {"key": anchor["id"], "name": anchor["label"], "manufacturer": "Danfoss"}
    matches = [{"key": p["id"], "name": p["name"], "manufacturer": comp_name.get(p["competitorId"], ""),
                "knownEquivalent": anchor["id"] in p.get("equivalentOf", []),
                "matchScore": None} for p in comps]

    # Gather source pages per product
    extra = list(payload.get("customFactors", [])) + [k for k in payload.get("factors", {})]
    blocks, valid = [], {}
    a_pages = select_pages(pages_for(anchor.get("datasets")), extra)
    blocks.append(pages_as_prompt(f"{anchor['label']} (Danfoss, key={anchor['id']})", a_pages))
    for p in comps:
        cp = select_pages(pages_for(p.get("datasets")), extra)
        blocks.append(pages_as_prompt(f"{comp_name.get(p['competitorId'])} {p['name']} (key={p['id']})", cp))
        for pg in cp:
            valid[(pg["fileId"], pg["page"])] = {"doc": pg["doc"], "title": f"{comp_name.get(p['competitorId'])} {p['name']}"}
    for pg in a_pages:
        valid[(pg["fileId"], pg["page"])] = {"doc": pg["doc"], "title": f"Danfoss {anchor['label']}"}

    base = {"title": f"{anchor['label']} vs. " + (", ".join(m["name"] for m in matches) or "no competitor data yet"),
            "isSample": False, "anchor": anchor_info, "matches": matches}

    pre_flags = []
    if not a_pages:
        pre_flags.append(issue("missing", f"No extracted datasheet for {anchor['label']}. Upload one via 'Upload data'."))
    if not comps:
        pre_flags.append(issue("missing", "No competitor datasheets uploaded for this category yet. "
                                          "Upload one in the Competitors step."))
    if auto and any(not m["knownEquivalent"] for m in matches):
        pre_flags.append(issue("unverified", "Competitor products were selected automatically. "
                                             "Confirm functional equivalence before showing them to the customer."))
    if not a_pages and not comps:
        return {**base, "attributes": [], "insights": {}, "sources": [], "flags": pre_flags,
                "confidence": {"level": "low", "reason": "No source documents available."}}

    factors = ", ".join(f"{f['id']} ({f['label']})" for f in cat["factors"])
    prompt = f"""{chr(10).join(blocks)}

Product keys: {json.dumps([anchor['id']] + [p['id'] for p in comps])}
Factor ids: {factors}
User priorities: {json.dumps(payload.get('factors', {}))}  (must = must-have, nice = nice-to-have)
Custom factors to include as rows (factor "custom"): {json.dumps(payload.get('customFactors', []))}
Buyer: {json.dumps(payload.get('buyer', {}), ensure_ascii=False)}
Questions/notes from the salesperson: {json.dumps(payload.get('chatNotes', []), ensure_ascii=False)}

Build a comparison of the most important technical attributes (flow, pressure, sections, ports, actuation,
dimensions/weight, operating conditions, plus every must-have and custom factor). Include a values entry
for EVERY product key in every row. Call submit_comparison."""

    try:
        resp = claude().messages.create(
            model=MODEL, max_tokens=8000, system=RULES,
            tools=[COMPARE_TOOL], tool_choice={"type": "tool", "name": "submit_comparison"},
            messages=[{"role": "user", "content": prompt}],
        )
        result = next(b.input for b in resp.content if b.type == "tool_use")
    except Exception as e:
        return {**base, "attributes": [], "insights": {}, "sources": [],
                "flags": pre_flags + [issue("error", f"AI comparison failed: {e}")],
                "confidence": {"level": "low", "reason": "The AI step failed."}}

    sources = build_sources(result, valid)
    for a in result.get("attributes", []):  # make sure every column has a cell
        for key in [anchor["id"]] + [p["id"] for p in comps]:
            a.setdefault("values", {}).setdefault(key, {"value": None, "status": "missing"})
    return {**base, "attributes": result.get("attributes", []), "insights": result.get("insights", {}),
            "flags": pre_flags + result.get("flags", []), "confidence": result.get("confidence", {}),
            "sources": sources}


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

    category = find_parent(cat, anchor["id"])
    comps, _ = pick_competitors(cat, ctx, anchor["id"], category["id"] if category else None)
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
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
