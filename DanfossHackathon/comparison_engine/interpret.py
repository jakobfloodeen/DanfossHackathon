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

`ask` can be replaced by any function ask(system, prompt, schema) -> str. Tests use a fake one.
"""
import hashlib
import inspect
import json
from typing import Callable

import pandas as pd

from . import config
from .facts import Fact, build_facts, code_differences
from .guardrails import OUTPUT_SCHEMA, add_missing_verify, check_interpretation
from .llm import LLMUnavailable, ask_claude, extract_json
from .logic import Logic, load_logic
from .repository import load_repository
from .results import STATUS_MESSAGES, AnalysisResult, comparison_rows, onepager_data
from .shortlist import shortlist as make_shortlist

PROMPT_VERSION = "2"            # change when the prompt text changes -> old cache entries are not reused
_MEMORY_CACHE: dict = {}

HARD_RULES = """HARD RULES (code checks every one of them; breaking one makes the answer fail):
1. Use ONLY the numbered facts below. No outside knowledge, no prices, no delivery times.
2. Every statement cites the fact ids it is based on (fact_ids / target_facts / candidate_facts).
3. Every number you write must appear in the facts you cite, or be a difference listed under
   DIFFERENCES, or come from the customer context/question. Use the normalised units shown.
4. target_facts = facts of the TARGET only; candidate_facts = facts of that point's candidate only.
5. candidate_value_is describes the numbers: higher / lower / similar (within {similar:.0%}) / n/a (text).
6. For attributes with a 'better' direction (listed below) the verdict must follow the numbers.
7. Facts with status missing / unverified / conflict / unknown unit go into 'verify'. Missing is not worse.
8. If no candidate does the same job as the target: comparable=false, best_match=null, give the reason.
9. Product ids must be copied exactly from the lists below.
10. Keep it SHORT - this becomes a one-page brief: best_match.why at most 40 words; every other "why"/"text"
    at most 30 words; at most 3 talking_points, 2 tradeoffs and 8 points. Order points by importance for this
    customer (most important first). Do not repeat the same fact in several places.
11. If the same quantity has DIFFERENT attribute names in the two products (e.g. "max_pressure" vs
    "max_pressure_continuous"), make a point that pairs them (target_facts + candidate_facts) and say in
    "why" if the definitions may differ. Never pair continuous with intermittent values.
12. Answer with ONE JSON object in exactly this format, nothing else:
{schema}"""


# ---------------------------------------------------------------- prompt
def _fact_line(f: Fact) -> str:
    original = f"{f.value} {f.unit or ''}".strip() if f.value is not None else "-"
    quote = (f.quote or "")[:140].replace("\n", " ")
    extra = f" | from {', '.join(f.based_on)}" if f.based_on else ""
    extra += f" | datasheet calls it '{f.attribute_original}'" if f.attribute_original else ""
    return (f"{f.id} | {f.product_id} | {f.attribute} | {f.display()} | original: {original} | "
            f"status: {f.status} | {f.source()} | \"{quote}\"{extra}")


ROLES = {   # pool -> (what the TARGET is, what the CANDIDATES are, default question)
    "danfoss": ("the competitor product the customer uses today", "Danfoss shortlist",
                "Which Danfoss product is the best replacement, and why?"),
    "competitors": ("the Danfoss product we start from", "competitor products",
                    "How does the Danfoss product compare with these competitor products?"),
}


def build_prompts(target_id: str, short: list[dict], facts: list[Fact], diffs, logic: Logic,
                  user_question: str, customer_context: str, focus: list[str] | None,
                  pool: str = "danfoss") -> tuple[str, str]:
    target_role, cand_role, default_question = ROLES[pool]
    better = ", ".join(f"{a}: {d} is better" for a, d in logic.settings["better"].items()) or "none given"
    system = (f"You compare a TARGET ({target_role}) with CANDIDATES ({cand_role}) for a Danfoss "
              "salesperson. Follow the LOGIC below, then the hard rules.\n\n=== LOGIC ===\n"
              + (logic.text or "(no logic file)") + "\n\n=== "
              + HARD_RULES.format(similar=float(logic.settings["similar_within"]),
                                  schema=json.dumps(OUTPUT_SCHEMA, indent=1))
              + f"\n\nBetter directions from the LOGIC file: {better}.")
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
        "FACTS (id | product | attribute | normalised value | original | status | source | quote):",
        *[_fact_line(f) for f in facts],
        "",
        "DIFFERENCES computed by code (candidate vs target, same attribute and unit):",
        *([f"- {d.candidate}: {d.text()} [{d.target_fact} vs {d.candidate_fact}]" for d in diffs] or ["- none"]),
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


def _ask_and_check(ask, system, prompt, check) -> tuple[dict | None, list[str], int]:
    """-> (answer or None, problems, attempts). One retry with the list of problems."""
    text = _call(ask, system, prompt)
    answer, problems = _parse_and_check(text, check)
    if not problems:
        return answer, [], 1
    retry = (prompt + "\n\nYOUR PREVIOUS ANSWER:\n" + str(text)[:20000]
             + "\n\nCODE FOUND THESE PROBLEMS - fix all of them and answer with the corrected JSON only:\n- "
             + "\n- ".join(problems))
    text2 = _call(ask, system, retry)
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
              use_cache: bool = True, pool: str = "danfoss") -> AnalysisResult:
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
    cand_ids = [c["product_id"] for c in short]
    facts = build_facts(target_id, cand_ids, data, logic)
    diffs = code_differences(facts, logic)
    result = AnalysisResult(
        status="no_ai", message="", target={"product_id": target_id, "brand": first["brand"],
                                            "product_name": first["product_name"], "family": first["family"]},
        shortlist=short, excluded=excluded, facts=facts, differences=diffs,
        meta={"model": config.llm_model(), "logic_files": logic.files, "cached": False, "attempts": 0,
              "focus_attributes": focus_attributes or [], "pool": pool})

    if not cand_ids:
        result.message = (f"No {ROLES[pool][1]} with data in the same family"
                          + (" passed the customer's must-haves." if must_haves else " yet."))
    elif not use_ai:
        result.message = STATUS_MESSAGES["no_ai"] + " (AI not requested)"
    else:
        sent = _focus_facts(facts, focus_attributes)
        system, prompt = build_prompts(target_id, short, sent, diffs, logic, user_question,
                                       customer_context, focus_attributes, pool)
        check = lambda a: check_interpretation(a, facts, diffs, target_id, cand_ids, logic,
                                               f"{user_question} {customer_context}")
        key = _cache_key(system, prompt)
        cached = _cache_get(key) if use_cache else None
        try:
            if cached and cached.get("answer") is not None and check(cached["answer"]):
                cached = None                   # fails today's (stricter) checks -> ask the AI again
            if cached:
                answer, attempts, problems = cached["answer"], cached["attempts"], cached["problems"]
                result.meta["cached"] = True
            else:
                answer, problems, attempts = _ask_and_check(ask or ask_claude, system, prompt, check)
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
