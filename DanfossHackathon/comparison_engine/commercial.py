"""
COMMERCIAL NOTES - approved commercial information next to the technical comparison.

The success criteria ask for "approved commercial information ... such as pricing, availability,
positioning, and selling points". These notes live in data/commercial/commercial.csv, each with its
source (sales guideline + slide), and carry an approval status:

    approved   an editor approved it -> shown in results and citable by the AI as K1, K2 ...
    pending    suggested by a viewer -> shown dimmed to the team, NEVER sent to the AI
    rejected   kept for the audit trail, shown nowhere

Who may do what comes from backend users (viewer suggests, editor approves) - the review-queue idea
from the main branch (hrub98), scoped to commercial facts.

A note's product_id is a catalog product id (pvg32) or a family id (valves, pumps, system):
family notes attach to every comparison in that family.
"""
import csv
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .facts import Fact

FIELDS = ["id", "product_id", "kind", "text", "regions", "source_file", "page", "status",
          "added_by", "approved_by", "approved_at"]
KINDS = ("positioning", "availability", "pricing", "selling_point", "transition", "warning")
_LOCK = threading.Lock()


def commercial_file() -> Path:
    return Path(os.getenv("COMMERCIAL_FILE", config.PROJECT_ROOT / "data" / "commercial" / "commercial.csv"))


def load_all() -> list[dict]:
    path = commercial_file()
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [{k: (r.get(k) or "").strip() for k in FIELDS} for r in csv.DictReader(f)]


def _save_all(rows: list[dict]) -> None:
    path = commercial_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in FIELDS} for r in rows)


def for_products(product_ids: list[str], family: str = "", status: str = "approved") -> list[dict]:
    """Notes for these products (target first, then candidates) and their family, in that order.
    Family notes match singular and plural ids (the catalog says 'valves', the engine 'valve')."""
    families = [family, family.rstrip("s"), family + "s"] if family else []
    wanted = [p for p in product_ids if p] + [f for f in dict.fromkeys(families) if f]
    rows = [r for r in load_all() if r["status"] == status and r["product_id"] in wanted]
    order = {pid: i for i, pid in enumerate(wanted)}
    return sorted(rows, key=lambda r: (order[r["product_id"]], r["id"]))


def facts(rows: list[dict], max_items: int = 6) -> list[Fact]:
    """Approved notes as citable facts K1..Kn (same pattern as the customer's C1..Cn)."""
    return [Fact(id=f"K{i}", product_id=r["product_id"], role="commercial", attribute=r["kind"] or "note",
                 value=r["text"], unit=None, norm_value=None, norm_unit=None, source_file=r["source_file"],
                 page=int(r["page"]) if str(r["page"]).isdigit() else None, quote=r["text"], status="ok",
                 note=(r["regions"] or ""))
            for i, r in enumerate(rows[:max_items], start=1)]


def commercial_facts(product_ids: list[str], family: str = "") -> list[Fact]:
    return facts(for_products(product_ids, family))


def add(product_id: str, kind: str, text: str, user: dict, source_file: str = "", page: str = "",
        regions: str = "") -> dict:
    """A new note. Editors' notes are approved at once; viewers' notes wait in the review queue."""
    if not (product_id and text.strip()):
        raise ValueError("A note needs a product (or family) and a text.")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    editor = user.get("role") == "editor"
    with _LOCK:
        rows = load_all()
        next_no = max((int(r["id"].split("-")[1]) for r in rows if r["id"].startswith("COM-")), default=0) + 1
        row = {"id": f"COM-{next_no:03d}", "product_id": product_id.strip(), "kind": kind,
               "text": " ".join(text.split()), "regions": regions.strip(),
               "source_file": source_file.strip() or f"added by {user.get('name', '?')}",
               "page": str(page).strip(), "status": "approved" if editor else "pending",
               "added_by": user.get("id", "?"),
               "approved_by": user.get("id", "") if editor else "", "approved_at": now if editor else ""}
        _save_all(rows + [row])
    return row


def decide(note_id: str, action: str, user: dict) -> dict:
    """Approve or reject a pending note. Editors only."""
    if user.get("role") != "editor":
        raise PermissionError("Only editors can approve or reject commercial notes.")
    if action not in ("approve", "reject"):
        raise ValueError("action must be approve or reject")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK:
        rows = load_all()
        for r in rows:
            if r["id"] == note_id:
                r["status"] = "approved" if action == "approve" else "rejected"
                r["approved_by"], r["approved_at"] = user.get("id", "?"), now
                _save_all(rows)
                return r
    raise KeyError(f"No note with id {note_id}")
