"""
COMPARISON ENGINE - old entry point, now a THIN WRAPPER around the `comparison_engine/` package (owner: JL).

New code should use the package directly:

    from comparison_engine import analyse
    result = analyse("competitorx pump 71", "Best Danfoss replacement?", "OEM, truck-mounted cranes")

This file keeps the old function names working until the website has switched:

    search(query)                     -> products matching free text (fuzzy). Empty -> offer PDF upload
    best_matches(product_id)          -> top-3 Danfoss alternatives with score + reason
    compare(id_a, id_b)               -> side-by-side table, rows R1..Rn, with sources + flags
    explain(comparison, customer)     -> AI talking points from ONLY that table + citation check (old style;
                                         the new comparison_engine.interpret() is stricter - use that)
    repository_report()               -> which CSVs were loaded / skipped and why
    load_repository()                 -> the whole repository as one clean table

Try it:   python engine.py examples/repository_demo [--explain]
"""
import os
import re
from pathlib import Path

import pandas as pd

from comparison_engine import config
from comparison_engine.facts import build_facts, code_differences
from comparison_engine.llm import ask_claude
from comparison_engine.logic import load_logic
from comparison_engine.repository import load_repository as _load_repository
from comparison_engine.repository import products as _products
from comparison_engine.repository import repository_report as _repository_report
from comparison_engine.results import comparison_rows
from comparison_engine.search import search as _search
from comparison_engine.shortlist import shortlist
from comparison_engine.units import BASE_UNIT, UNITS, fmt, parse_number  # noqa: F401 - re-exported
from comparison_engine.units import normalize as _normalize

ROOT = Path(__file__).parent
RULES_DIR = ROOT / "rules"
COLUMNS = config.COLUMNS
REQUIRED = config.REQUIRED


def to_number(value):
    """Kept for old callers. NOTE: now strict - text like '350 bar' or 'PC, LS' gives None."""
    return parse_number(value)


def normalize(value, unit):
    """-> (number in base unit, base unit) or (None, None) - the old tuple format."""
    n = _normalize(value, unit)
    return (n.value, n.unit) if n.value is not None else (None, None)


def load_rules(family: str) -> tuple[dict, str]:
    """rules/_general.md + rules/<family>.md -> (settings for code, text for the AI)."""
    logic = load_logic(family)
    return logic.settings, logic.text


def load_repository(repo_dir=None) -> pd.DataFrame:
    return _load_repository(repo_dir)


def repository_report(repo_dir=None) -> pd.DataFrame:
    return _repository_report(repo_dir)


def products(data=None) -> pd.DataFrame:
    return _products(data)


def search(query: str, limit: int = 10, min_score=None) -> pd.DataFrame:
    return _search(query, limit=limit)


def best_matches(target_id: str, top: int = 3, must_haves: dict | None = None,
                 weights: dict | None = None) -> pd.DataFrame:
    """Top Danfoss alternatives (code ranking). LOGIC hard_filters are hints now: see the 'flags' column."""
    s = shortlist(target_id, must_haves=must_haves, weights=weights)
    return s[["product_id", "score", "compared_on", "reason", "flags"]].head(top).reset_index(drop=True)


def compare(id_a: str, id_b: str) -> pd.DataFrame:
    """Side-by-side table. A = usually Danfoss, B = competitor. Rows R1..Rn for citations."""
    data = load_repository()
    family = data.loc[data["product_id"] == id_b, "family"].iloc[0]
    logic = load_logic(family)
    facts = build_facts(id_b, [id_a], data, logic)
    diffs = code_differences(facts, logic)
    brand = {pid: data.loc[data["product_id"] == pid, "brand"].iloc[0] for pid in (id_a, id_b)}
    by_id = {f.id: f for f in facts}

    def status(side):
        sts = side["statuses"]
        return "conflict" if "conflict" in sts else (sts[0] if sts else "missing")

    def quote(side):
        return "; ".join(by_id[i].quote or "" for i in side["facts"]) or None

    def source(side):
        return "; ".join(s["text"] for s in side["sources"] if s["file"]) if status(side) != "missing" else ""

    rows = []
    for i, r in enumerate(comparison_rows(facts, diffs, id_b, id_a), start=1):
        a, b, d = r["candidate"], r["target"], r["diff"]
        if d is None:
            verdict = r["code_verdict"]
        elif d["candidate_value_is"] == "similar":
            verdict = "similar"
        elif d["better"] in ("candidate", "target"):
            verdict = f"{brand[id_a] if d['better'] == 'candidate' else brand[id_b]} better ({d['diff_pct']:+.0f}%)"
        else:
            verdict = f"{d['diff_pct']:+.0f}%"
        rows.append({"id": f"R{i}", "attribute": r["attribute"],
                     "a": "not in source" if status(a) == "missing" else a["display"],
                     "b": "not in source" if status(b) == "missing" else b["display"],
                     "verdict": verdict, "a_source": source(a), "b_source": source(b),
                     "a_status": status(a), "b_status": status(b), "a_quote": quote(a), "b_quote": quote(b)})
    table = pd.DataFrame(rows)
    table.attrs.update(a=id_a, b=id_b, family=family)
    return table


# ---------------------------------------------------------------- old-style AI notes (kept for compatibility)
def check_answer(text: str, table: pd.DataFrame) -> list[str]:
    """Code checks the AI: every bullet cites existing rows, every number exists in the table."""
    problems, ids = [], set(table["id"])
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("-", "*", "•")) or re.match(r"^\d+[.)]\s", s):
            cites = re.findall(r"\bR\d+\b", s)
            if not cites:
                problems.append(f"bullet without citation: '{s[:70]}'")
            problems += [f"cites unknown row {c}" for c in cites if c not in ids]
    allowed = set()
    for col in ("a", "b", "verdict"):
        for v in table[col].astype(str):
            allowed |= {abs(n) for n in (parse_number(x) for x in re.findall(r"\d[\d.,]*\d|\d", v)) if n is not None}
    body = re.sub(r"\bR\d+\b", "", text)
    for x in re.findall(r"\d[\d.,]*\d|\d", body):
        n = parse_number(x)
        if n is None or n <= 10:
            continue
        if not any(abs(n - v) <= 0.01 * max(v, 1) for v in allowed):
            problems.append(f"number {x} is not in the comparison table")
    return problems


def explain(table: pd.DataFrame, customer_context: str = "", ask=None) -> dict:
    """Old-style AI notes on a compare() table (one retry). Prefer comparison_engine.interpret()."""
    ask = ask or (lambda system, prompt: ask_claude(system, prompt))
    prose = load_logic(table.attrs.get("family", "")).text
    system = ("You help a Danfoss salesperson prepare a customer meeting. Follow this skill:\n\n" + prose +
              "\n\nHard rules: use ONLY the comparison rows given. Cite row ids like [R2] in every bullet. "
              "Never invent numbers, prices or facts. Missing data is 'not stated in source', not worse.")
    rows = table.drop(columns=["a_quote", "b_quote"]).to_dict("records")
    prompt = (f"Product A: {table.attrs.get('a')}\nProduct B: {table.attrs.get('b')}\n"
              f"Customer context: {customer_context or 'none'}\n\nComparison rows:\n{rows}\n\n"
              "Write: 'Key differences' (3 bullets), 'Talking points for this customer' (3 bullets), "
              "'Trade-offs / risks' (2 bullets), 'Verify before the meeting' (every missing/unverified/conflict row).")
    text = ask(system, prompt)
    problems = check_answer(text, table)
    if problems:
        text = ask(system, prompt + "\n\nYour previous answer had these problems, fix them:\n- " +
                   "\n- ".join(problems) + "\n\nPrevious answer:\n" + text)
        problems = check_answer(text, table)
    return {"text": text, "problems": problems}


# ---------------------------------------------------------------- try it: python engine.py <repo folder>
if __name__ == "__main__":
    import sys
    pd.set_option("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 60)
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        os.environ["REPO_DIR"] = sys.argv[1]
    print("REPOSITORY\n", repository_report()[["file", "rows", "loaded", "reason"]], "\n")
    data = load_repository()
    comp_products = products(data)
    target = comp_products[comp_products["brand"] != "Danfoss"]["product_id"].iloc[0]
    print("SEARCH 'competitorx pump71'\n", search("competitorx pump71"), "\n")
    m = best_matches(target)
    print(f"BEST MATCHES for {target}\n", m, "\n")
    t = compare(m["product_id"].iloc[0], target)
    print("COMPARISON\n", t[["id", "attribute", "a", "b", "verdict", "a_source", "b_source", "a_status", "b_status"]])
    if "--explain" in sys.argv:
        try:
            r = explain(t, "OEM, truck-mounted cranes")
            print("\nAI NOTES\n", r["text"], "\nPROBLEMS:", r["problems"])
        except Exception as e:  # noqa: BLE001
            print(f"\nAI NOTES not available: {e}")
