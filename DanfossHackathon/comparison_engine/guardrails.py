"""
GUARDRAILS - code checks the AI's answer. Nothing the AI says reaches the user unchecked.

    problems = check_interpretation(answer, facts, diffs, target_id, candidate_ids, logic, user_text)

Checks (each problem is one plain-English string):
  1. shape    the JSON has every field, with the right types and allowed words (OUTPUT_SCHEMA)
  2. products every product id is the target or a shortlisted candidate
  3. facts    every cited fact id exists and belongs to the right product
  4. citation every statement cites at least one fact
  5. numbers  every number in a statement appears in its cited facts (or in the code-computed
              difference between them, or in the user's own question/context) - small tolerance
  6. verdicts where code can compute the difference itself (one number each side, same unit), the AI's
              'candidate_value_is' must match, and 'similar' / better-or-worse must agree with code
  7. honesty  no best match -> comparable must be false and a reason must be given
  6b. pairing a point may not pair a number with a text value, or numbers of different kinds (bar vs l/min)
  8. length   one-page brief: at most 3 talking points, 2 trade-offs, 8 points; statements short
              (limits are a bit above what the prompt asks for, so a 31-word sentence is not an error)
add_missing_verify() is not a check: it adds every unverified/conflict/missing fact of the target and
best match to the verify list if the AI forgot one (marked "added by code").
"""
import re

from .facts import Diff, Fact, compare_numbers, facts_by_id
from .logic import Logic
from .units import parse_number

VERDICTS = ["candidate_better", "target_better", "similar", "trade_off", "not_comparable", "unclear"]
VALUE_IS = ["higher", "lower", "similar", "n/a"]
CONFIDENCE = ["high", "medium", "low"]
NEEDS_CHECK = {"unverified", "conflict", "missing", "unknown unit"}

_cited = {"type": "object", "additionalProperties": False, "required": ["text", "fact_ids"],
          "properties": {"text": {"type": "string"}, "fact_ids": {"type": "array", "items": {"type": "string"}}}}
BUSINESS_KINDS = ["opportunity", "fit", "displace", "risk"]
_business = {"type": "object", "additionalProperties": False, "required": ["kind", "text", "fact_ids"],
             "properties": {"kind": {"type": "string", "enum": BUSINESS_KINDS}, "text": {"type": "string"},
                            "fact_ids": {"type": "array", "items": {"type": "string"}}}}
_priority = {"type": "object", "additionalProperties": False, "required": ["factor", "text", "fact_ids"],
             "properties": {"factor": {"type": "string"}, "text": {"type": "string"},
                            "fact_ids": {"type": "array", "items": {"type": "string"}}}}
_product_choice = {"type": "object", "additionalProperties": False, "required": ["product_id", "why", "fact_ids"],
                   "properties": {"product_id": {"type": "string"}, "why": {"type": "string"},
                                  "fact_ids": {"type": "array", "items": {"type": "string"}}}}

# The JSON the AI must return. Sent to the API as a structured-output schema AND checked here by code.
OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["comparable", "not_comparable_reason", "best_match", "alternatives", "points",
                 "application_fit", "talking_points", "tradeoffs", "business_fit", "priority_fit", "verify",
                 "overall_confidence"],
    "properties": {
        "comparable": {"type": "boolean"},
        "not_comparable_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "best_match": {"anyOf": [_product_choice, {"type": "null"}]},
        "alternatives": {"type": "array", "items": _product_choice},
        "points": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["topic", "candidate", "target_facts", "candidate_facts", "candidate_value_is",
                         "verdict", "why", "confidence"],
            "properties": {
                "topic": {"type": "string"},
                "candidate": {"type": "string"},
                "target_facts": {"type": "array", "items": {"type": "string"}},
                "candidate_facts": {"type": "array", "items": {"type": "string"}},
                "candidate_value_is": {"type": "string", "enum": VALUE_IS},
                "verdict": {"type": "string", "enum": VERDICTS},
                "why": {"type": "string"},
                "confidence": {"type": "string", "enum": CONFIDENCE}}}},
        "application_fit": _cited,
        "talking_points": {"type": "array", "items": _cited},
        "tradeoffs": {"type": "array", "items": _cited},
        "business_fit": {"type": "array", "items": _business},
        "priority_fit": {"type": "array", "items": _priority},
        "verify": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["fact_id", "reason"],
            "properties": {"fact_id": {"type": "string"}, "reason": {"type": "string"}}}},
        "overall_confidence": {"type": "string", "enum": CONFIDENCE},
    },
}


# ---------------------------------------------------------------- 1. shape
def _type_ok(value, schema: dict) -> bool:
    if "anyOf" in schema:
        return any(_type_ok(value, s) for s in schema["anyOf"])
    t = schema.get("type")
    return {"object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str), "boolean": isinstance(value, bool),
            "null": value is None}.get(t, True)


def check_shape(value, schema: dict = OUTPUT_SCHEMA, path: str = "answer") -> list[str]:
    """Small JSON-schema check (only what OUTPUT_SCHEMA uses). Returns problems."""
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if _type_ok(value, s)]
        return check_shape(value, options[0], path) if options else [f"{path}: wrong type"]
    if not _type_ok(value, schema):
        return [f"{path}: should be {schema.get('type')}"]
    problems = []
    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: '{value}' is not one of {', '.join(schema['enum'])}")
    if schema.get("type") == "object":
        for key in schema.get("required", []):
            if key not in value and not (path == "answer" and key in OPTIONAL_IN_OLD_ANSWERS):
                problems.append(f"{path}: missing field '{key}'")
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                problems += check_shape(value[key], sub, f"{path}.{key}")
    if schema.get("type") == "array":
        for i, item in enumerate(value):
            problems += check_shape(item, schema["items"], f"{path}[{i}]")
    return problems


# ---------------------------------------------------------------- 5. numbers
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.,])[-+]?\d+(?:[.,]\d+)*(?![A-Za-z0-9_])")


def numbers_in(text: str, ignore: list[str] = ()) -> list[str]:
    """Stand-alone numbers in a text. Fact ids (F12), model codes (A10VO71), units (cm3, min-1) and
    the given product names are ignored - they are names, not claims."""
    s = text or ""
    for name in sorted(ignore, key=len, reverse=True):
        if name:       # "pvg32" also matches "PVG 32" / "PVG-32" (letters and digits written apart)
            parts = re.split(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", name)
            body = r"[\s-]?".join(re.escape(x) for x in parts)       # whole names only: "6" must not eat "2026"
            s = re.sub(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", " ", s, flags=re.I)
    s = re.sub(r"\b(?:in3|cm3|m3|min-1|1/min|r/min)\b", " ", s, flags=re.I)
    return [m.group().lstrip("+-").rstrip(".,") for m in _NUMBER.finditer(s)]


def _allowed_numbers(fact_ids: list[str], by_id: dict[str, Fact], diffs: list[Diff]) -> set[float]:
    allowed: set[float] = set()
    cited = [by_id[i] for i in fact_ids if i in by_id]
    for f in cited:
        if f.role in BUSINESS_ROLES:                     # a passage/note: every number in its text counts
            text = f"{f.value or ''} {f.note or ''}" if f.role == "profile" else (f.value or "")   # + news date
            allowed |= {abs(v) for v in (parse_number(x) for x in numbers_in(text)) if v is not None}
            continue
        for f2 in [f] + [by_id[b] for b in f.based_on if b in by_id]:      # calculated: inputs count too
            for v in (f2.norm_value, parse_number(f2.value)):
                if v is not None:
                    allowed.add(abs(float(v)))
    nums = [f for f in cited if f.is_number()]
    for a in nums:                                       # differences between any two cited numbers
        for b in nums:
            if a is not b and a.norm_unit == b.norm_unit:
                allowed.add(abs(a.norm_value - b.norm_value))
                allowed.add(abs(a.norm_value - b.norm_value) / max(abs(b.norm_value), 1e-9) * 100)
                allowed.add(abs(a.norm_value / b.norm_value) if b.norm_value else 0)
    ids = set(fact_ids)
    for d in diffs:
        if {d.target_fact, d.candidate_fact} & ids:
            allowed.add(abs(d.diff_pct))
    return allowed


def _number_ok(n: float, allowed: set[float]) -> bool:
    return any(abs(n - v) <= max(0.015 * abs(v), 0.51) for v in allowed)


def logic_numbers(logic: Logic) -> set[float]:
    """Thresholds from the LOGIC file, as the AI sees them: 0.03 -> 3 (%), 0.25 -> 25, 0.9 -> 90."""
    s = logic.settings
    values = [s.get("similar_within"), s.get("conflict_tolerance"), s.get("size_window")]
    for group in (s["hard_filters"].get("within") or {}, s["hard_filters"].get("at_least") or {}):
        values += list(group.values())
    return {round(float(v) * 100, 3) for v in values if isinstance(v, (int, float))}


# ---------------------------------------------------------------- the main check
def _statements(answer: dict) -> list[tuple[str, str, list[str], str | None]]:
    """-> list of (where, text, cited fact ids, product the facts must belong to or None)."""
    out = []
    bm = answer.get("best_match")
    if isinstance(bm, dict):
        out.append(("best_match.why", bm.get("why", ""), bm.get("fact_ids", []), bm.get("product_id")))
    for i, alt in enumerate(answer.get("alternatives") or []):
        out.append((f"alternatives[{i}].why", alt.get("why", ""), alt.get("fact_ids", []), alt.get("product_id")))
    for i, p in enumerate(answer.get("points") or []):
        out.append((f"points[{i}].why", p.get("why", ""),
                    list(p.get("target_facts") or []) + list(p.get("candidate_facts") or []), p.get("candidate")))
    fit = answer.get("application_fit")
    if isinstance(fit, dict) and (fit.get("text") or "").strip():
        out.append(("application_fit", fit.get("text", ""), fit.get("fact_ids", []), None))
    for key in ("talking_points", "tradeoffs", "business_fit", "priority_fit"):
        for i, t in enumerate(answer.get(key) or []):
            out.append((f"{key}[{i}]", t.get("text", ""), t.get("fact_ids", []), None))
    for i, v in enumerate(answer.get("verify") or []):
        out.append((f"verify[{i}].reason", v.get("reason", ""), [v.get("fact_id")], None))
    return out


def check_interpretation(answer: dict, facts: list[Fact], diffs: list[Diff], target_id: str,
                         candidate_ids: list[str], logic: Logic, user_text: str = "") -> list[str]:
    problems = check_shape(answer)
    if problems:
        return problems                       # the rest needs the right shape
    by_id = facts_by_id(facts)
    allowed_products = set(candidate_ids)
    names = [target_id] + list(candidate_ids)
    names += [n.split("|", 1)[-1] for n in names]                              # product names
    names += [f.value for f in facts if f.value and "code" in f.attribute]     # model codes
    user_numbers = {abs(v) for v in (parse_number(x) for x in numbers_in(user_text)) if v is not None}
    user_numbers |= logic_numbers(logic)          # thresholds the AI was told, e.g. "similar = within 3 %"

    # 2. products
    bm = answer["best_match"]
    if bm is not None and bm["product_id"] not in allowed_products:
        problems.append(f"best_match: '{bm['product_id']}' is not on the shortlist")
    for i, alt in enumerate(answer["alternatives"]):
        if alt["product_id"] not in allowed_products:
            problems.append(f"alternatives[{i}]: '{alt['product_id']}' is not on the shortlist")
    for i, p in enumerate(answer["points"]):
        if p["candidate"] not in allowed_products:
            problems.append(f"points[{i}]: candidate '{p['candidate']}' is not on the shortlist")

    # 7. honesty about comparability
    if bm is None and answer["comparable"]:
        problems.append("best_match is empty but comparable is true")
    if not answer["comparable"] and not (answer["not_comparable_reason"] or "").strip():
        problems.append("comparable is false but not_comparable_reason is empty")

    # 3. + 4. + 5. facts, citations, numbers
    for where, text, ids, product in _statements(answer):
        if not ids or not any(ids):
            problems.append(f"{where}: no fact cited")
            continue
        for fid in ids:
            if fid not in by_id:
                problems.append(f"{where}: cites unknown fact {fid}")
            elif product and by_id[fid].role not in BUSINESS_ROLES \
                    and by_id[fid].product_id not in (product, target_id):
                problems.append(f"{where}: {fid} belongs to {by_id[fid].product_id}, not {product} or the target")
        allowed = _allowed_numbers(ids, by_id, diffs) | user_numbers
        for raw in numbers_in(text, names):
            n = parse_number(raw)
            if n is not None and not _number_ok(abs(n), allowed):
                problems.append(f"{where}: number {raw} is not in the cited facts {', '.join(ids)}")

    # 9. business points must rest on something about THIS customer (profile, customer file or approved note)
    for i, b in enumerate(answer.get("business_fit") or []):
        if not any(by_id[x].role in BUSINESS_ROLES for x in b.get("fact_ids", []) if x in by_id):
            problems.append(f"business_fit[{i}]: cite at least one customer fact (P.., C..) or approved note (K..)")

    # 8. length
    problems += check_length(answer)

    # 3. facts on the right side of a point + 6. verdicts
    similar = float(logic.settings["similar_within"])
    for i, p in enumerate(answer["points"]):
        for fid in p["target_facts"]:
            if fid in by_id and by_id[fid].product_id != target_id:
                problems.append(f"points[{i}].target_facts: {fid} is not a fact of the target")
        for fid in p["candidate_facts"]:
            if fid in by_id and by_id[fid].product_id != p["candidate"]:
                problems.append(f"points[{i}].candidate_facts: {fid} is not a fact of {p['candidate']}")
        problems += _check_pairing(i, p, by_id)
        problems += _check_verdict(i, p, by_id, logic, similar)
    return problems


MAX_COUNT = {"talking_points": 3, "tradeoffs": 2, "points": 8, "business_fit": 6, "priority_fit": 10}
MAX_WORDS_BUSINESS = 75     # business points connect customer + product + what to say: room for detail
BUSINESS_ROLES = ("customer", "commercial", "profile")    # facts ABOUT the customer / sales guidelines, not products
OPTIONAL_IN_OLD_ANSWERS = ("business_fit", "priority_fit")   # answers cached before the fields existed still pass
MAX_WORDS, MAX_WORDS_BEST_MATCH = 45, 55


def check_length(answer: dict) -> list[str]:
    problems = [f"{key}: {len(answer[key])} items, at most {limit} allowed"
                for key, limit in MAX_COUNT.items() if len(answer.get(key) or []) > limit]
    for where, text, _, _ in _statements(answer):
        limit = (MAX_WORDS_BEST_MATCH if where == "best_match.why" else
                 MAX_WORDS_BUSINESS if where.startswith(("business_fit", "priority_fit")) else MAX_WORDS)
        if len((text or "").split()) > limit:
            problems.append(f"{where}: {len(text.split())} words, keep it under {limit - 15}")
    return problems


def _check_pairing(i: int, p: dict, by_id: dict[str, Fact]) -> list[str]:
    """Like with like: numbers with numbers of the same kind, text with text (missing facts are ignored)."""
    t = [by_id[x] for x in p["target_facts"] if x in by_id and by_id[x].status != "missing"]
    c = [by_id[x] for x in p["candidate_facts"] if x in by_id and by_id[x].status != "missing"]
    if not t or not c:
        return []
    kinds = lambda fs: {(f.norm_unit or "number") if f.is_number() else "text" for f in fs}  # noqa: E731
    if not kinds(t) & kinds(c):
        return [f"points[{i}]: pairs {', '.join(sorted(kinds(t)))} ({', '.join(f.attribute for f in t)}) with "
                f"{', '.join(sorted(kinds(c)))} ({', '.join(f.attribute for f in c)}) - compare like with like"]
    return []


def _check_verdict(i: int, p: dict, by_id: dict[str, Fact], logic: Logic, similar: float) -> list[str]:
    if len(p["target_facts"]) != 1 or len(p["candidate_facts"]) != 1:
        return []
    t, c = by_id.get(p["target_facts"][0]), by_id.get(p["candidate_facts"][0])
    if not t or not c or not (t.is_number() and c.is_number()) or t.norm_unit != c.norm_unit:
        return []
    if "conflict" in (t.status, c.status):
        return []
    pct, word = compare_numbers(t.norm_value, c.norm_value, similar)
    problems = []
    if p["candidate_value_is"] != word:
        problems.append(f"points[{i}]: candidate_value_is '{p['candidate_value_is']}' but code computes "
                        f"'{word}' ({c.display()} vs {t.display()}, {pct:+.1f}%)")
    if p["verdict"] == "similar" and word != "similar":
        problems.append(f"points[{i}]: verdict 'similar' but the difference is {pct:+.1f}% "
                        f"(similar = within ±{similar:.0%})")
    direction = logic.settings["better"].get(t.attribute) if t.attribute == c.attribute else None
    if direction in ("higher", "lower") and word != "similar" and p["verdict"] in ("candidate_better", "target_better", "similar"):
        expected = "candidate_better" if word == direction else "target_better"
        if p["verdict"] != expected:
            problems.append(f"points[{i}]: verdict '{p['verdict']}' but for {t.attribute} {direction} is better, "
                            f"so code expects '{expected}' ({c.display()} vs {t.display()})")
    return problems


def add_missing_verify(answer: dict, facts: list[Fact], target_id: str) -> dict:
    """Every doubtful fact of the target and the best match must be in 'verify'. Code adds what's missing."""
    bm = answer.get("best_match") if isinstance(answer, dict) else None
    products = {target_id} | ({bm.get("product_id")} if isinstance(bm, dict) else set())
    listed = {v.get("fact_id") for v in answer.get("verify") or [] if isinstance(v, dict)}
    for f in facts:
        if f.product_id in products and f.status in NEEDS_CHECK and f.id not in listed:
            answer.setdefault("verify", []).append(
                {"fact_id": f.id, "reason": f"status '{f.status}' ({f.attribute}) - added by code"})
    return answer


def grounding_summary(answer: dict | None, facts: list[Fact]) -> dict:
    """What the code verified in the final answer - shown to the user, not just checked internally:
    how many AI statements there are, how many facts they cite, and how many numbers appear in them.
    Together with the problems count this is the 'nothing uncited, nothing unchecked' meter."""
    if not answer:
        return {}
    by_id = facts_by_id(facts)
    statements = citations = numbers = 0
    for _where, text, ids, _product in _statements(answer):
        statements += 1
        citations += len({i for i in ids if i in by_id})
        numbers += len(numbers_in(text))
    return {"statements": statements, "citations": citations, "numbers": numbers}
