"""
WEBSITE ADAPTER - turns an engine result into the exact JSON the website draws
(frontend/README.md, "POST /api/compare" response; example: frontend/examples/compare_response.json).

    response = to_website(result, names=..., doc_url=..., pending=...)

No FastAPI in here: backend.py calls this function and returns the dict.

How engine statuses become website statuses (one cell = one product + one attribute):
    ok / manual            -> ok                (unit changed by our code, e.g. psi -> bar -> "converted")
    unverified             -> unverified        (quote not confirmed on the page)
    conflict               -> conflict          (ALL source values are shown in the cell note)
    calculated             -> inferred          (formula + input facts in the note)
    unknown unit           -> unverified        (original value shown, not compared)
    missing / no fact      -> missing           (value null - never guessed)
    uploaded, no CSV yet   -> pending           ("Awaiting extraction")
"""
from collections import Counter
from typing import Callable

from .facts import Fact
from .results import AnalysisResult
from .units import UNITS, fmt, unit_key

MAX_MATCHES = 3

# attribute name contains ... -> the website's priority chip ("factor")
FACTOR_WORDS = [
    ("flow", "flow"), ("displacement", "flow"), ("speed", "flow"),
    ("pressure", "pressure"),
    ("port", "ports"), ("thread", "ports"),
    ("can", "electronics"), ("actuation", "electronics"), ("electr", "electronics"), ("control", "electronics"),
    ("voltage", "electronics"), ("current", "electronics"),
    ("temperature", "environment"), ("viscosity", "environment"), ("clean", "environment"),
    ("filtration", "environment"),
    ("weight", "size"), ("mass", "size"), ("dimension", "size"), ("length", "size"), ("width", "size"),
    ("height", "size"), ("pitch", "size"),
    ("price", "price"), ("lead_time", "availability"), ("availability", "availability"),
    ("life", "lifetime"), ("warranty", "support"),
]
STATUS_LEVEL = {"conflict": "conflict", "missing": "missing", "unverified": "unverified",
                "unknown unit": "unverified", "calculated": "inferred"}


def factor_for(attribute: str) -> str:
    a = attribute.lower()
    return next((factor for word, factor in FACTOR_WORDS if word in a), "options")


def label_for(attribute: str) -> str:
    return attribute.replace("_", " ").strip().capitalize()


class _Sources:
    """Numbers sources 1, 2, 3 ... per (document, page), like the website expects."""

    def __init__(self, doc_url: Callable | None):
        self.doc_url = doc_url or (lambda file, page: None)
        self.items: list[dict] = []
        self.index: dict[tuple, int] = {}

    def ref(self, f: Fact, by_id: dict[str, Fact]) -> list[int]:
        if f.source_file == "calculated":                      # cite what it was calculated from
            return sorted({n for b in f.based_on if b in by_id for n in self.ref(by_id[b], by_id)})
        if not f.source_file:
            return []
        key = (f.source_file, f.page)
        if key not in self.index:
            self.index[key] = len(self.items) + 1
            self.items.append({"id": self.index[key], "title": f.source_file, "doc": "", "quotes": [],
                               "page": str(f.page) if f.page else "", "date": "",
                               "url": self.doc_url(f.source_file, f.page)})
        item = self.items[self.index[key] - 1]
        if f.quote and f.quote not in item["quotes"]:
            item["quotes"].append(f.quote)
        return [self.index[key]]

    def refs(self, facts: list[Fact], by_id: dict[str, Fact]) -> list[int]:
        return sorted({n for f in facts for n in self.ref(f, by_id)})

    def finish(self) -> list[dict]:
        for item in self.items:                                 # show the exact quotes under each source
            quotes = item.pop("quotes")
            item["doc"] = " · ".join(f'"{q}"' for q in quotes[:4]) + (" …" if len(quotes) > 4 else "")
        return self.items


def _row_unit(facts: list[Fact]) -> str:
    units = Counter(f.norm_unit for f in facts if f.is_number() and f.norm_unit)
    return units.most_common(1)[0][0] if units else ""


def _value_text(f: Fact, row_unit: str) -> str:
    if f.is_number() and (f.norm_unit or "") == row_unit:
        return fmt(f.norm_value)                                # the unit is shown in the row label
    return f"{f.value} {f.unit or ''}".strip()


def _cell(facts: list[Fact], row_unit: str, by_id: dict[str, Fact], sources: _Sources) -> dict:
    """One table cell from all facts of one product + attribute."""
    stated = [f for f in facts if f.status != "missing"]
    if not stated:
        where = ", ".join(sorted({f.source_file for f in facts if f.source_file}))
        return {"value": None, "status": "missing", "sources": [],
                "note": f"Not stated in {where}" if where else "Not in the extracted data"}
    if any(f.status == "conflict" for f in stated):
        return {"value": " / ".join(dict.fromkeys(_value_text(f, row_unit) for f in stated)),
                "status": "conflict", "sources": sources.refs(stated, by_id),
                "note": "Sources disagree: " + "; ".join(f"{_value_text(f, row_unit)} ({f.source()})" for f in stated)}
    f = stated[0]
    cell = {"value": _value_text(f, row_unit), "status": "ok", "sources": sources.refs([f], by_id)}
    if f.status == "calculated":
        cell.update(status="inferred", note=f"Calculated by code: {f.quote} (from {', '.join(f.based_on)})")
    elif f.status == "unverified":
        cell.update(status="unverified", note="Quote not confirmed on the cited page")
    elif f.status == "unknown unit":
        cell.update(status="unverified", note=f"Unknown unit '{f.unit}' - not compared")
    elif f.is_number() and UNITS.get(unit_key(f.unit), (None, 1))[1] != 1:     # real conversion, not cc = cm3
        cell.update(status="converted", note=f"Original: {f.value} {f.unit}")
    elif f.status == "manual":
        cell["note"] = "Corrected / confirmed by a person"
    return cell


def _columns(result: AnalysisResult) -> list[str]:
    """Which candidates get a column: the AI's best match + alternatives, else the code ranking."""
    interp = result.interpretation or {}
    picked = []
    if isinstance(interp.get("best_match"), dict):
        picked.append(interp["best_match"].get("product_id"))
    picked += [a.get("product_id") for a in interp.get("alternatives", []) if isinstance(a, dict)]
    picked += [c["product_id"] for c in result.shortlist]
    allowed = {c["product_id"] for c in result.shortlist}
    return [p for p in dict.fromkeys(picked) if p in allowed][:MAX_MATCHES]


def to_website(result: AnalysisResult, names: dict[str, dict] | None = None,
               doc_url: Callable | None = None, pending: dict[str, str] | None = None,
               extra_flags: list[dict] | None = None) -> dict:
    """
    names    product_id -> {"name", "manufacturer", "knownEquivalent"} (from the website catalog)
    doc_url  function(source_file, page) -> link to the stored PDF page, or None
    pending  product_id -> note, for products that have uploaded files but no facts CSV yet
    """
    names, pending = names or {}, pending or {}
    facts = result.facts
    by_id = {f.id: f for f in facts}
    sources = _Sources(doc_url)
    target = (result.target or {}).get("product_id")
    scores = {c["product_id"]: c.get("score") for c in result.shortlist}

    def column(pid: str) -> dict:
        info = names.get(pid, {})
        brand = info.get("manufacturer") or (pid.split("|")[0] if "|" in pid else "")
        name = info.get("name") or (pid.split("|", 1)[-1] if "|" in pid else pid)
        col = {"key": pid, "name": name, "manufacturer": brand,
               "knownEquivalent": bool(info.get("knownEquivalent"))}
        if pid != target:
            col["matchScore"] = None if scores.get(pid) is None else round(scores[pid] / 100, 2)
        return col

    waiting = [p for p in pending if p != target]               # picked by the user: always shown
    match_ids = [p for p in _columns(result) if p not in waiting][:max(1, MAX_MATCHES - len(waiting))]
    match_ids += waiting
    keys = ([target] if target else []) + match_ids

    attributes = []
    for attr in dict.fromkeys(f.attribute for f in facts if f.product_id in keys):
        row_facts = [f for f in facts if f.attribute == attr and f.product_id in keys]
        unit = _row_unit(row_facts)
        values = {}
        for pid in keys:
            mine = [f for f in row_facts if f.product_id == pid]
            if mine:
                values[pid] = _cell(mine, unit, by_id, sources)
            elif pid in pending:
                values[pid] = {"value": None, "status": "pending", "note": pending[pid]}
            else:
                values[pid] = {"value": None, "status": "missing", "sources": [], "note": "Not in the extracted data"}
        attributes.append({"id": attr, "factor": factor_for(attr), "label": label_for(attr),
                           "unit": unit, "values": values})

    insights = _insights(result, by_id, sources, names, match_ids)
    flags = _flags(result, by_id, names, pending) + list(extra_flags or [])
    anchor = column(target) if target else {"key": "none", "name": "?", "manufacturer": ""}
    matches = [column(p) for p in match_ids]
    return {
        "title": f"{anchor['name']} vs. " + (", ".join(m["name"] for m in matches) or "no comparable product with data yet"),
        "isSample": bool(result.onepager and result.onepager.get("demo_data")),
        "anchor": {k: v for k, v in anchor.items() if k != "matchScore"},
        "matches": matches,
        "attributes": attributes,
        "insights": insights,
        "flags": flags,
        "confidence": _confidence(result),
        "sources": sources.finish(),
        "engine": {"status": result.status, "message": result.message, "best_match": result.best_match_id,
                   "problems": result.problems, "meta": result.meta},
    }


def _name(pid: str, names: dict) -> str:
    return names.get(pid, {}).get("name") or (pid.split("|", 1)[-1] if "|" in (pid or "") else pid)


def _insights(result, by_id, sources, names, match_ids) -> dict:
    interp = result.interpretation
    if interp:
        def item(text, ids):
            return {"text": text, "sources": sources.refs([by_id[i] for i in ids if i in by_id], by_id)}
        bm = interp.get("best_match") or {}
        differences = ([item(f"Closest match: {_name(bm.get('product_id'), names)}. {bm.get('why', '')}",
                             bm.get("fact_ids", []))] if bm else [])
        if not interp.get("comparable", True) and interp.get("not_comparable_reason"):
            differences.append({"text": f"Not comparable: {interp['not_comparable_reason']}", "sources": []})
        differences += [item(f"{p['topic']}: {p['why']}", p["target_facts"] + p["candidate_facts"])
                        for p in interp.get("points", [])]
        benefits = [item(t["text"], t["fact_ids"]) for t in interp.get("talking_points", [])]
        fit = interp.get("application_fit") or {}
        if fit.get("text"):
            benefits.append(item(fit["text"], fit.get("fact_ids", [])))
        tradeoffs = [item(t["text"], t["fact_ids"]) for t in interp.get("tradeoffs", [])]
        return {"differences": differences, "benefits": benefits, "tradeoffs": tradeoffs}
    # no AI: only differences that code computed itself
    first = match_ids[0] if match_ids else None
    differences = []
    for d in result.differences:
        if d.candidate != first:
            continue
        t, c = by_id[d.target_fact], by_id[d.candidate_fact]
        differences.append({
            "text": (f"{label_for(d.attribute)}: {_name(first, names)} {fmt(d.candidate_value)} vs "
                     f"{_name(t.product_id, names)} {fmt(d.target_value)} {d.unit} ({d.diff_pct:+.1f}%, "
                     f"{d.candidate_value_is})"),
            "sources": sources.refs([t, c], by_id)})
    return {"differences": differences, "benefits": [], "tradeoffs": []}


def _flags(result, by_id, names, pending) -> list[dict]:
    flags = []
    if result.status == "no_ai":
        flags.append({"level": "warning", "text": result.message})
    for p in result.problems:
        flags.append({"level": "warning", "text": f"Code check of the AI answer: {p}"})
    verify = (result.onepager or {}).get("verify") or []
    seen, unverified = set(), {}
    for v in verify:
        f = by_id.get((v.get("fact") or {}).get("id"))
        if not f or (f.product_id, f.attribute, f.status) in seen:     # a conflict has 2+ facts: flag it once
            continue
        seen.add((f.product_id, f.attribute, f.status))
        if f.status in ("unverified", "unknown unit"):                 # many of these: one line per product
            unverified.setdefault(f.product_id, []).append(label_for(f.attribute))
            continue
        flags.append({"level": STATUS_LEVEL.get(f.status, "warning"),
                      "text": f"{_name(f.product_id, names)} · {label_for(f.attribute)}: {v.get('reason')}"})
    for pid, labels in unverified.items():
        flags.append({"level": "unverified",
                      "text": f"{_name(pid, names)}: {len(labels)} value(s) not confirmed by a quote on the page - "
                              f"check before quoting: {', '.join(labels)}."})
    for e in result.excluded:
        flags.append({"level": "warning", "text": f"{_name(e['product_id'], names)} left out: {e['reason']}"})
    for pid, note in pending.items():
        flags.append({"level": "pending", "text": f"{_name(pid, names)}: {note}"})
    if (result.onepager or {}).get("demo_data"):
        flags.append({"level": "sample", "text": "Contains DEMO data (made up for testing) - not for customers."})
    return flags


def _confidence(result: AnalysisResult) -> dict:
    if result.interpretation:
        level = result.interpretation.get("overall_confidence", "medium")
        if result.problems:
            return {"level": "low", "reason": f"AI answer has {len(result.problems)} problem(s) found by code."}
        return {"level": level, "reason": "AI interpretation of sourced facts, every claim checked by code."}
    return {"level": "low", "reason": "No AI interpretation - sourced facts and code-computed differences only."}
