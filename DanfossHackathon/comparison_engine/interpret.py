"""
INTERPRET - the ONE AI step, wrapped in code checks.

    result = interpret(target_id, candidates, user_question, customer_context, focus_attributes=None)

1. code builds the fact sheet (facts.py) and the code-computed differences
2. ONE call to the AI with: LOGIC text (rules/_general.md + rules/<family>.md), the facts, the differences,
   the customer context and the question. The AI must answer with JSON in a fixed format (OUTPUT_SCHEMA).
3. code checks the answer (guardrails.py). Problems -> ONE retry with the list of problems.
   Still problems -> the result is returned WITH a visible 'problems' list (status 'ok_with_problems').
4. no API key / API error / timeout -> status 'no_ai': facts + differences + code ranking, and a message.
5. answers are cached by a hash of the inputs (memory + .cache/interpret/*.json), so the same question
   twice costs nothing and a stored answer can be replayed in the demo.
6. token diet (the AI is the only thing that costs money per comparison):
   - fact lines are short: no quotes (code checks quotes, the AI does not need them), no repeated values
   - code numbers / ordering codes are never sent (they are not comparison facts)
   - only the top PROMPT_MAX_CANDIDATES candidates (default 3 = the columns the website shows)
   - per product at most PROMPT_MAX_FACTS facts (default 40), attributes another product also has first;
     at most PROMPT_MAX_UNSHARED (default 10) that only this product has (lean_facts)
   - the AI lists at most 3 'verify' items; code adds the rest (add_missing_verify) - output tokens cost 5x input
   - the answer format is enforced by the API (structured output), so the schema is not repeated in the text
   - the ONE retry can use a stronger model (LLM_RETRY_MODEL): cheap model first, strong model only if needed

`ask` can be replaced by any function ask(system, prompt, schema) -> str. Tests use a fake one.
"""
import functools
import hashlib
import inspect
import json
import re
from typing import Callable

import pandas as pd

from . import commercial, customer_profile, config
from .facts import Fact, build_facts, code_differences
from .guardrails import OUTPUT_SCHEMA, add_missing_verify, check_interpretation, grounding_summary
from .llm import LLMUnavailable, ask_claude, extract_json
from .logic import Logic, load_logic
from .repository import load_repository
from .results import STATUS_MESSAGES, AnalysisResult, comparison_rows, onepager_data
from .shortlist import shortlist as make_shortlist

PROMPT_VERSION = "8"            # change when the prompt text changes -> old cache entries are not reused
_MEMORY_CACHE: dict = {}

HARD_RULES = """HARD RULES (code checks every one of them; breaking one makes the answer fail):
1. Use ONLY the numbered facts below. No outside knowledge, no prices, no delivery times.
2. Every statement cites the fact ids it is based on (fact_ids / target_facts / candidate_facts).
3. Every number you write must appear in the facts you cite, or be a difference listed under
   DIFFERENCES, or come from the customer context/question. Use the normalised units shown.
4. target_facts = facts of the TARGET only; candidate_facts = facts of that point's candidate only.
5. candidate_value_is describes the numbers: higher / lower / similar (within {similar:.0%}) / n/a (text).
6. For attributes with a 'better' direction (listed below) the verdict must follow the numbers.
7. 'verify': at most 3 entries - the doubtful facts (missing / unverified / conflict / unknown unit) that matter
   most for THIS customer. Code adds all other doubtful facts itself; do not list them. Missing is not worse.
8. If no candidate does the same job as the target: comparable=false, best_match=null, give the reason.
9. Product ids must be copied exactly from the lists below.
10. Keep it SHORT - this becomes a one-page brief: best_match.why at most 40 words; every other "why"/"text"
    at most 30 words; at most 3 talking_points, 2 tradeoffs and 8 points. Order points by importance for this
    customer (most important first). Do not repeat the same fact in several places.
11. If the same quantity has DIFFERENT attribute names in the two products (e.g. "max_pressure" vs
    "max_pressure_continuous"), make a point that pairs them (target_facts + candidate_facts) and say in
    "why" if the definitions may differ. Never pair continuous with intermittent values.
12. CUSTOMER passages (C1, C2 ...) are what the customer told us. Cite them in best_match, talking_points,
    tradeoffs and application_fit when you use them - never in target_facts / candidate_facts. They are not
    product data. Numbers from a customer passage only when you cite that passage.
13. COMMERCIAL notes (K1, K2 ...) are APPROVED sales-guideline statements (positioning, availability,
    pricing, transitions). They are the ONLY allowed source of commercial claims. Use at most 2, only where
    they help THIS customer, cite them like customer passages (never in target_facts / candidate_facts),
    and never go beyond what a note says. A note of kind "warning" or "transition" about an involved
    product MUST be mentioned (talking_points or tradeoffs).
14. CUSTOMER PROFILE facts (P1, P2 ...) are public statements from the customer's OWN website, verified by
    code: what markets they serve, what machines they build, what they say their priorities are. Use them to
    tailor the business points and, where it helps, the talking points. Cite them like customer passages
    (never in target_facts / candidate_facts). Never infer budgets, buying intent or current suppliers.
15. business_fit: 0-5 bullets for a salesperson who knows selling but not hydraulics or this customer. Each
    bullet names something specific about the customer (their machine, product line, stated priority, market,
    plan - P / C facts or the brief), the product property that matters for it (F facts, with the value), and
    what that means in the conversation. Be specific, not generic; up to 60 words each. Every bullet cites at
    least one P, C or K fact. No customer information at all -> an empty list.
16. Answer with ONE JSON object in the response format you are given (fields: comparable,
    not_comparable_reason, best_match, alternatives, points, application_fit, talking_points, tradeoffs,
    business_fit, verify, overall_confidence), nothing else."""


# ---------------------------------------------------------------- prompt
def _fact_line(f: Fact) -> str:
    """One short line per fact. Quotes are NOT sent: code verifies them, the AI only needs id + value."""
    original = f"{f.value} {f.unit or ''}".strip() if f.value is not None else ""
    shown = f.display()
    parts = [f.id, f.product_id, f.attribute, shown]
    if original and original != shown:
        parts.append(f"orig {original}")                 # only when the unit was converted
    parts.append(f.status)
    if f.source_file == "calculated":
        parts.append(f.source())
    if f.based_on:
        parts.append(f"from {', '.join(f.based_on)}")
    if f.attribute_original:
        parts.append(f"datasheet name '{f.attribute_original}'")
    if f.condition:
        parts.append(f"condition: {f.condition}")
    return " | ".join(parts)


NOISE = ("code_number", "ordering_code", "part_number")       # never comparison facts


def lean_facts(facts: list[Fact], logic: Logic, focus: list[str] | None, max_per_product: int,
               max_unshared: int | None = None, context: str = "") -> list[Fact]:
    """The facts worth sending to the AI, per product:
    - never code numbers / ordering codes
    - attributes that another product ALSO has (the AI can compare them) come first, up to max_per_product
    - attributes only this product has: at most max_unshared (default 10), so the AI can still pair values
      that the datasheets name differently. Picked by: focus attributes, words from the customer's brief
      (e.g. 'flow', 'pressure'), standard attributes from the LOGIC file, trusted status.
    Facts that a kept calculated fact is based on are always kept, so every 'from F..' reference still
    points at a sent fact."""
    max_unshared = config.prompt_max_unshared() if max_unshared is None else max_unshared
    facts = [f for f in facts if not f.attribute.startswith(NOISE)]
    wanted = {a.strip().lower().replace(" ", "_") for a in focus or []}
    words = {w for w in re.findall(r"[a-z]{4,}", context.lower())}
    owners: dict[str, set] = {}
    for f in facts:
        owners.setdefault(f.attribute, set()).add(f.product_id)
    standard = set(logic.settings.get("better", {})) | set((logic.settings.get("attribute_aliases") or {}).values())
    rank = lambda f: (f.attribute not in wanted, not any(w in f.attribute for w in words),     # noqa: E731
                      f.attribute not in standard, f.status not in config.TRUSTED)
    keep: set[str] = set()
    by_product: dict[str, list[Fact]] = {}
    for f in facts:
        by_product.setdefault(f.product_id, []).append(f)
    for group in by_product.values():
        shared = sorted([f for f in group if len(owners[f.attribute]) > 1], key=rank)[:max_per_product]
        room = min(max_unshared, max_per_product - len(shared))
        own = sorted([f for f in group if len(owners[f.attribute]) == 1], key=rank)[:max(room, 0)]
        keep |= {f.id for f in shared + own}
    keep |= {b for f in facts if f.id in keep for b in f.based_on}
    return [f for f in facts if f.id in keep]


ROLES = {   # pool -> (what the TARGET is, what the CANDIDATES are, default question)
    "danfoss": ("the competitor product the customer uses today", "Danfoss shortlist",
                "Which Danfoss product is the best replacement, and why?"),
    "competitors": ("the Danfoss product we start from", "competitor products",
                    "How does the Danfoss product compare with these competitor products?"),
    "system": ("the Danfoss system of components", "competitor system",
               "How does the Danfoss system compare with the competitor system, as a whole and per component?"),
    "system_reverse": ("the competitor system the customer uses today", "Danfoss system",
                       "Which Danfoss system replaces it, and how do they compare as a whole and per component?"),
}


def build_prompts(target_id: str, short: list[dict], facts: list[Fact], diffs, logic: Logic,
                  user_question: str, customer_context: str, focus: list[str] | None,
                  pool: str = "danfoss", customer: list[Fact] | None = None,
                  commercial_notes: list[Fact] | None = None,
                  profile: list[Fact] | None = None) -> tuple[str, str]:
    target_role, cand_role, default_question = ROLES[pool]
    better = ", ".join(f"{a}: {d} is better" for a, d in logic.settings["better"].items()) or "none given"
    system = (f"You compare a TARGET ({target_role}) with CANDIDATES ({cand_role}) for a Danfoss "
              "salesperson. Follow the LOGIC below, then the hard rules.\n\n=== LOGIC ===\n"
              + (logic.text or "(no logic file)") + "\n\n=== "
              + HARD_RULES.format(similar=float(logic.settings["similar_within"]))
              + f"\n\nBetter directions from the LOGIC file: {better}.")
    sent_ids = {f.id for f in facts}
    cand_lines = [f"- {c['product_id']} | code score {c.get('score', '-')} | {c.get('reason', '')}"
                  + (f" | flags: {'; '.join(c['flags'])}" if c.get("flags") else "") for c in short]
    prompt = "\n".join([
        f"CUSTOMER CONTEXT: {customer_context or 'none given'}",
        f"QUESTION: {user_question or default_question}",
        f"FOCUS ON: {', '.join(focus)}" if focus else "FOCUS ON: everything relevant",
        "",
        f"TARGET ({target_role}): {target_id}",
        f"CANDIDATES ({cand_role}, pre-ranked by code - the score is only a hint):",
        *cand_lines,
        "",
        "FACTS (id | product | attribute | normalised value | [orig value if converted] | status | ...):",
        *[_fact_line(f) for f in facts],
        "",
        "DIFFERENCES computed by code (candidate vs target, same attribute and unit):",
        *([f"- {d.candidate}: {d.text()} [{d.target_fact} vs {d.candidate_fact}]" for d in diffs
           if not sent_ids or {d.target_fact, d.candidate_fact} <= sent_ids] or ["- none"]),
        "",
        "CUSTOMER FILES (what the customer told us - cite as C1, C2 ...):",
        *([f"{c.id} | {c.source_file}" + (f" p.{c.page}" if c.page else "") + f' | "{c.value}"' for c in customer or []]
          or ["- none uploaded"]),
        "",
        "APPROVED COMMERCIAL NOTES (from the sales guidelines - cite as K1, K2 ...; rule 13):",
        *([f"{k.id} | {k.product_id} | {k.attribute}" + (f" | {k.note}" if k.note and k.note != "all" else "")
           + f' | "{k.value}" | {k.source_file}' + (f" slide {k.page}" if k.page else "") for k in commercial_notes or []]
          or ["- none approved"]),
        "",
        "CUSTOMER PROFILE (from the customer's own website, verified - cite as P1, P2 ...; rule 14):",
        *([f"{f.id} | {f.attribute} | {f.value}" + (f" ({f.note})" if f.note else "") for f in profile or []]
          or ["- not researched"]),
    ])
    return system, prompt


def _focus_facts(facts: list[Fact], focus: list[str] | None) -> list[Fact]:
    if not focus:
        return facts
    wanted = {a.strip().lower().replace(" ", "_") for a in focus}
    keep = {f.id for f in facts if f.attribute in wanted}
    keep |= {b for f in facts if f.id in keep for b in f.based_on}
    return [f for f in facts if f.id in keep]


# ---------------------------------------------------------------- cache
def _cache_key(system: str, prompt: str) -> str:
    raw = json.dumps({"v": PROMPT_VERSION, "model": config.llm_model(), "system": system, "prompt": prompt},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    folder = config.cache_dir()
    mem = _MEMORY_CACHE.get((str(folder), key))
    if mem:
        return mem
    path = folder / f"{key}.json"
    if path.exists():
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            _MEMORY_CACHE[(str(folder), key)] = entry
            return entry
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _cache_put(key: str, entry: dict) -> None:
    folder = config.cache_dir()
    _MEMORY_CACHE[(str(folder), key)] = entry
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / ".gitignore").exists():         # cache files are never committed, in any repo
            (folder / ".gitignore").write_text("*\n", encoding="utf-8")
        (folder / f"{key}.json").write_text(json.dumps(entry, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass                                    # caching is a nice-to-have, never a reason to fail


# ---------------------------------------------------------------- the AI call with checks
def _call(ask: Callable, system: str, prompt: str) -> str:
    params = inspect.signature(ask).parameters
    if len(params) >= 3 or any(p.kind == p.VAR_POSITIONAL for p in params.values()):
        return ask(system, prompt, OUTPUT_SCHEMA)
    return ask(system, prompt)


def _ask_and_check(ask, system, prompt, check, retry_ask=None) -> tuple[dict | None, list[str], int]:
    """-> (answer or None, problems, attempts). One retry with the list of problems
    (retry_ask: e.g. the same call on a stronger model; default = ask)."""
    text = _call(ask, system, prompt)
    answer, problems = _parse_and_check(text, check)
    if not problems:
        return answer, [], 1
    retry = (prompt + "\n\nYOUR PREVIOUS ANSWER:\n" + str(text)[:20000]
             + "\n\nCODE FOUND THESE PROBLEMS - fix all of them and answer with the corrected JSON only:\n- "
             + "\n- ".join(problems))
    text2 = _call(retry_ask or ask, system, retry)
    answer2, problems2 = _parse_and_check(text2, check)
    if answer2 is None and answer is not None:
        return answer, problems + ["retry answer could not be read - showing the first answer"], 2
    return answer2, problems2, 2


def _parse_and_check(text, check) -> tuple[dict | None, list[str]]:
    try:
        answer = extract_json(text)
    except ValueError as e:
        return None, [str(e)]
    return answer, check(answer)


# ---------------------------------------------------------------- public function
def _other_family_hint(family: str, data: pd.DataFrame, pool: str) -> str:
    """Gap management: when a family has no candidates, say where data DOES exist instead of a dead end."""
    side = data[data["brand"].str.contains("danfoss", case=False, na=False)] if pool == "danfoss" \
        else data[~data["brand"].str.contains("danfoss", case=False, na=False)]
    others = sorted(set(side["family"].dropna()) - {family, "system"})
    if not others:
        return ""
    return (f" {ROLES[pool][1].capitalize()} data exists for: {', '.join(others)} - upload a datasheet of "
            f"this family (Upload data) or compare at system level, where families combine.")


def _candidate_list(target_id, candidates, must_haves, data, pool) -> tuple[list[dict], list[dict]]:
    if candidates is None:
        s = make_shortlist(target_id, must_haves=must_haves, data=data, pool=pool)
        return s.to_dict("records"), s.attrs.get("excluded", [])
    if isinstance(candidates, pd.DataFrame):
        return candidates.to_dict("records"), candidates.attrs.get("excluded", [])
    return [c if isinstance(c, dict) else {"product_id": c} for c in candidates], []


def interpret(target_id: str, candidates=None, user_question: str = "", customer_context: str = "",
              focus_attributes: list[str] | None = None, ask: Callable | None = None, use_ai: bool = True,
              must_haves: dict | None = None, data: pd.DataFrame | None = None,
              use_cache: bool = True, pool: str = "danfoss",
              customer_facts: list[Fact] | None = None,
              commercial_notes: list[Fact] | None = None,
              profile_facts: list[Fact] | None = None) -> AnalysisResult:
    """Compare the target with candidates (ids, a shortlist DataFrame, or None = make the shortlist).
    pool = "danfoss": target is a competitor product, candidates are Danfoss products (default).
    pool = "competitors": target is a Danfoss product, candidates are competitor products.
    Returns an AnalysisResult; never raises for AI problems (falls back to the code-only result)."""
    data = load_repository() if data is None else data
    target_rows = data[data["product_id"] == target_id]
    if target_rows.empty:
        return AnalysisResult(status="not_found", message=STATUS_MESSAGES["not_found"], query=target_id)
    first = target_rows.iloc[0]
    logic = load_logic(first["family"])
    short, excluded = _candidate_list(target_id, candidates, must_haves, data, pool)
    short = short[:config.prompt_max_candidates()]   # the website shows 3 columns: the AI only needs those
    cand_ids = [c["product_id"] for c in short]
    facts = build_facts(target_id, cand_ids, data, logic)
    diffs = code_differences(facts, logic)
    customer = list(customer_facts or [])                 # C1, C2 ... from the customer's own files
    profile = list(profile_facts or [])                   # P1, P2 ... from the customer's own website
    other_notes: list[dict] = []
    if commercial_notes is None:                          # K1, K2 ... approved notes that fit THIS customer
        terms, regions = customer_profile.terms_from(profile, f"{customer_context} {user_question}")
        names = [first["product_name"]] + [c.get("product_name", "") for c in short]
        shown, other_notes = commercial.relevant_notes(target_id, cand_ids, first["family"], names, terms, regions)
        commercial_notes = commercial.facts(shown)
        for f, r in zip(commercial_notes, shown):
            f.condition = r["why"]                        # why this note was picked (shown on hover)
    facts = facts + customer + list(commercial_notes) + profile
    result = AnalysisResult(
        status="no_ai", message="", target={"product_id": target_id, "brand": first["brand"],
                                            "product_name": first["product_name"], "family": first["family"]},
        shortlist=short, excluded=excluded, facts=facts, differences=diffs,
        meta={"model": config.llm_model(), "logic_files": logic.files, "cached": False, "attempts": 0,
              "focus_attributes": focus_attributes or [], "pool": pool, "customer_passages": len(customer),
              "commercial_notes": len(commercial_notes), "profile_facts": len(profile),
              "commercial_other": other_notes})

    if not cand_ids:
        result.message = (f"No {ROLES[pool][1]} with data in the same family"
                          + (" passed the customer's must-haves." if must_haves else " yet.")
                          + _other_family_hint(first["family"], data, pool))
    elif not use_ai:
        result.message = STATUS_MESSAGES["no_ai"] + " (AI not requested)"
    else:
        sent = lean_facts(_focus_facts([f for f in facts if f.role not in ("customer", "commercial", "profile")],
                                       focus_attributes),
                          logic, focus_attributes, config.prompt_max_facts(),
                          context=f"{user_question} {customer_context}")
        result.meta["facts_sent"] = len(sent)
        system, prompt = build_prompts(target_id, short, sent, diffs, logic, user_question,
                                       customer_context, focus_attributes, pool, customer, commercial_notes,
                                       profile)
        check = lambda a: check_interpretation(a, facts, diffs, target_id, cand_ids, logic,
                                               f"{user_question} {customer_context}")
        key = _cache_key(system, prompt)
        cached = _cache_get(key) if use_cache else None
        try:
            if cached and cached.get("answer") is not None and \
                    sorted(check(cached["answer"])) != sorted(cached.get("problems") or []):
                cached = None                   # today's checks find something new -> ask the AI again
                                                # (same known problems -> reuse: asking again costs money
                                                #  and usually returns the same answer)
            if cached:
                answer, attempts, problems = cached["answer"], cached["attempts"], cached["problems"]
                result.meta["cached"] = True
            else:
                if ask is None:                 # real API: first model, then (maybe stronger) retry model
                    first = functools.partial(ask_claude, model=config.llm_model())
                    again = functools.partial(ask_claude, model=config.llm_retry_model())
                    answer, problems, attempts = _ask_and_check(first, system, prompt, check, again)
                else:
                    answer, problems, attempts = _ask_and_check(ask, system, prompt, check)
                if answer is not None and use_cache:
                    _cache_put(key, {"answer": answer, "problems": problems, "attempts": attempts})
            result.meta["attempts"] = attempts
            if answer is None:
                result.problems = problems
                result.message = STATUS_MESSAGES["no_ai"] + " (the AI answer could not be read)"
            else:
                result.interpretation = add_missing_verify(json.loads(json.dumps(answer)), facts, target_id)
                result.problems = problems
                result.status = "ok_with_problems" if problems else "ok"
                result.message = STATUS_MESSAGES[result.status]
                result.meta["grounding"] = {**grounding_summary(answer, facts),
                                            "problems": len(problems), "attempts": attempts}
        except LLMUnavailable as e:
            result.message = f"{STATUS_MESSAGES['no_ai']} Reason: {e}."
        except Exception as e:  # noqa: BLE001 - the website must keep working whatever the AI does
            result.message = f"{STATUS_MESSAGES['no_ai']} Reason: unexpected AI error ({type(e).__name__})."

    result.best_match_id = _best_match(result, cand_ids)
    result.comparison_rows = comparison_rows(facts, diffs, target_id, result.best_match_id, result.interpretation)
    result.onepager = onepager_data(result, customer_context, user_question)
    return result


def _best_match(result: AnalysisResult, cand_ids: list[str]) -> str | None:
    if result.interpretation is not None:
        bm = result.interpretation.get("best_match")
        if isinstance(bm, dict) and bm.get("product_id") in cand_ids and result.interpretation.get("comparable", True):
            return bm["product_id"]
        return None
    return cand_ids[0] if cand_ids else None          # no AI: the code ranking's top candidate
