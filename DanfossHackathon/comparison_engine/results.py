"""
RESULTS - the one object the website gets back, plus the data for the comparison table and one-pager.

    result = analyse(...)            # or interpret(...)
    result.status                    # "ok" / "ok_with_problems" / "no_ai" / "not_found" / "needs_choice"
    result.to_dict()                 # plain JSON-able dict (for Flask/FastAPI/Streamlit)

The engine does NOT draw anything or make PDFs. It gives the UI:
    comparison_rows  one row per attribute: target vs best match, every value with its source link
    onepager         headline, recommendation + why, key rows, talking points with sources, verify list
"""
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from urllib.parse import quote as url_quote

from .facts import Diff, Fact, facts_by_id

STATUS_MESSAGES = {
    "ok": "AI interpretation checked by code - no problems found.",
    "ok_with_problems": "AI interpretation has problems that code could not fix - see 'problems'.",
    "no_ai": "AI interpretation not available. Showing sourced facts and code-computed differences only.",
    "not_found": "Product not found in the repository. Upload its datasheet (PDF) to add it.",
    "needs_choice": "Several products match - please pick one.",
}


def doc_link(source_file: str | None, page: int | None) -> str | None:
    """Link to the stored datasheet at the cited page. Prefix from env DOCS_URL_PREFIX
    (default '/app/static/docs/' = Streamlit static serving). None for calculated values."""
    if not source_file or source_file == "calculated":
        return None
    prefix = os.getenv("DOCS_URL_PREFIX", "/app/static/docs/")
    return f"{prefix}{url_quote(source_file)}" + (f"#page={page}" if page else "")


def source_ref(f: Fact) -> dict:
    return {"fact_id": f.id, "file": f.source_file, "page": f.page, "quote": f.quote,
            "status": f.status, "link": doc_link(f.source_file, f.page), "text": f.source()}


@dataclass
class AnalysisResult:
    status: str
    message: str
    query: str | None = None
    search_hits: list[dict] = field(default_factory=list)
    target: dict | None = None
    shortlist: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    differences: list[Diff] = field(default_factory=list)
    interpretation: dict | None = None
    problems: list[str] = field(default_factory=list)
    best_match_id: str | None = None
    comparison_rows: list[dict] = field(default_factory=list)
    onepager: dict | None = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["facts"] = [f.to_dict() for f in self.facts]
        d["differences"] = [x.to_dict() for x in self.differences]
        return d


# ---------------------------------------------------------------- comparison table
def comparison_rows(facts: list[Fact], diffs: list[Diff], target_id: str, candidate_id: str | None,
                    interpretation: dict | None = None) -> list[dict]:
    """Target vs one candidate. One row per attribute (same name), plus rows where the AI paired
    attributes with different names. Every value keeps its source + status."""
    if not candidate_id:
        return []
    by_id = facts_by_id(facts)
    points = [p for p in (interpretation or {}).get("points", []) if p.get("candidate") == candidate_id]
    diff_by_attr = {d.attribute: d for d in diffs if d.candidate == candidate_id}

    def side(fs: list[Fact]) -> dict:
        if not fs:
            return {"display": "not in repository", "facts": [], "sources": [], "statuses": ["missing"]}
        shown = [f.display() for f in fs]
        display = " / ".join(dict.fromkeys(shown)) + (" (sources disagree)" if any(f.status == "conflict" for f in fs) else "")
        return {"display": display, "facts": [f.id for f in fs], "sources": [source_ref(f) for f in fs],
                "statuses": sorted({f.status for f in fs})}

    def ai_for(t_ids: set, c_ids: set) -> dict | None:
        for p in points:
            if set(p.get("target_facts", [])) & t_ids or set(p.get("candidate_facts", [])) & c_ids:
                return {k: p.get(k) for k in ("topic", "verdict", "candidate_value_is", "why", "confidence")}
        return None

    order = list(dict.fromkeys(f.attribute for f in facts if f.product_id in (target_id, candidate_id)))
    rows = []
    for attr in order:
        t = [f for f in facts if f.product_id == target_id and f.attribute == attr]
        c = [f for f in facts if f.product_id == candidate_id and f.attribute == attr]
        d = diff_by_attr.get(attr)
        rows.append({"attribute": attr, "target": side(t), "candidate": side(c),
                     "code_verdict": d.text().split("= ", 1)[-1] if d else _no_diff_reason(t, c),
                     "diff": d.to_dict() if d else None,
                     "ai": ai_for({f.id for f in t}, {f.id for f in c}),
                     "flags": sorted(({f.status for f in t + c} | ({"missing"} if not (t and c) else set()))
                                     - {"ok", "manual"})})
    for p in points:                                  # AI paired differently named attributes
        t = [by_id[i] for i in p.get("target_facts", []) if i in by_id]
        c = [by_id[i] for i in p.get("candidate_facts", []) if i in by_id]
        t_attrs, c_attrs = {f.attribute for f in t}, {f.attribute for f in c}
        if t and c and t_attrs != c_attrs:
            rows.append({"attribute": f"{' + '.join(sorted(t_attrs))} ↔ {' + '.join(sorted(c_attrs))}",
                         "target": side(t), "candidate": side(c), "code_verdict": "paired by AI",
                         "diff": None, "ai": ai_for({f.id for f in t}, {f.id for f in c}),
                         "flags": sorted({f.status for f in t + c} - {"ok", "manual"}) + ["paired by AI"]})
    return rows


def _no_diff_reason(t: list[Fact], c: list[Fact]) -> str:
    if not t or not c or all(f.status == "missing" for f in t) or all(f.status == "missing" for f in c):
        return "missing data"
    if any(f.status == "conflict" for f in t + c):
        return "check: sources disagree"
    if any(f.status == "unknown unit" for f in t + c):
        return "check: unknown unit"
    if all(f.norm_value is None for f in t + c):
        return "text - compare by reading"
    return "not directly comparable (different units or several values)"


# ---------------------------------------------------------------- one-pager data
def _resolve(items: list[dict], by_id: dict[str, Fact], text_key: str = "text") -> list[dict]:
    return [{"text": it.get(text_key, ""), "sources": [source_ref(by_id[i]) for i in it.get("fact_ids", []) if i in by_id]}
            for it in items or []]


def onepager_data(result: AnalysisResult, customer_context: str, user_question: str) -> dict:
    """Everything a one-pager needs, as plain data. The UI decides how it looks (PDF, HTML, ...)."""
    by_id = facts_by_id(result.facts)
    interp = result.interpretation or {}
    best = result.best_match_id
    bm = interp.get("best_match") or {}
    shortlist_row = next((s for s in result.shortlist if s["product_id"] == best), {})
    target_id = (result.target or {}).get("product_id")
    verify = interp.get("verify") if result.interpretation else [
        {"fact_id": f.id, "reason": f"status '{f.status}'"} for f in result.facts
        if f.product_id in (target_id, best) and f.status in ("unverified", "conflict", "missing", "unknown unit")]
    return {
        "title": f"{best.split('|', 1)[-1] if best else 'No match'} vs {target_id or '?'}",
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "customer_context": customer_context, "question": user_question,
        "status": result.status, "message": result.message, "problems": result.problems,
        "target": result.target,
        "recommended": {"product_id": best,
                        "why": bm.get("why") if result.interpretation else (
                            f"Code ranking only (no AI): {shortlist_row.get('reason', '')}" if best else None),
                        "sources": [source_ref(by_id[i]) for i in bm.get("fact_ids", []) if i in by_id]},
        "alternatives": [{"product_id": a.get("product_id"), "why": a.get("why"),
                          "sources": [source_ref(by_id[i]) for i in a.get("fact_ids", []) if i in by_id]}
                         for a in interp.get("alternatives", [])],
        "rows": [r for r in result.comparison_rows if r["code_verdict"] != "missing data" or r["ai"]],
        "talking_points": _resolve(interp.get("talking_points", []), by_id),
        "tradeoffs": _resolve(interp.get("tradeoffs", []), by_id),
        "application_fit": _resolve([interp["application_fit"]], by_id)[0] if interp.get("application_fit") else None,
        "verify": [{"fact": by_id[v["fact_id"]].to_dict() if v.get("fact_id") in by_id else None,
                    "reason": v.get("reason")} for v in verify or []],
        "confidence": interp.get("overall_confidence", "not assessed (no AI)"),
        "demo_data": any("DEMO" in (f.source_file or "") or "DEMO" in (f.product_id or "") for f in result.facts),
    }
