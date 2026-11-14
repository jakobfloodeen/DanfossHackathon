"""
CLI demo:

    python -m comparison_engine examples/repository_demo "Which Danfoss pump replaces it?"
    python -m comparison_engine examples/repository_demo "question" --product "compettorx 71" \
        --customer "OEM, truck-mounted cranes" --must rated_pressure=250 --live

Without --live no AI is called (free, works without a key): you see the code-only result.
With --live one Claude call is made (needs ANTHROPIC_API_KEY in .env); answers are cached.
"""
import argparse
import json
import os
import sys

from .units import parse_number


def _must_haves(items: list[str]) -> dict:
    out = {}
    for item in items:
        attr, _, value = item.partition("=")
        number = parse_number(value)
        out[attr.strip()] = number if number is not None else value.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m comparison_engine", description="Competitor comparison demo")
    p.add_argument("repo", help="folder with the product CSVs")
    p.add_argument("question", nargs="?", default="Which Danfoss product is the best replacement, and why?")
    p.add_argument("--product", help="search text or product_id (default: first non-Danfoss product)")
    p.add_argument("--customer", default="", help="customer context, e.g. 'OEM, truck-mounted cranes'")
    p.add_argument("--must", action="append", default=[], help="must-have, e.g. rated_pressure=250 (repeatable)")
    p.add_argument("--focus", action="append", default=[], help="attribute to focus on (repeatable)")
    p.add_argument("--live", action="store_true", help="call the AI (costs money, needs a key)")
    p.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = p.parse_args(argv)

    os.environ["REPO_DIR"] = args.repo
    from . import analyse, products, repository_report        # import after REPO_DIR is set

    report = repository_report()
    print("REPOSITORY")
    for _, r in report.iterrows():
        print(f"  {'OK  ' if r['loaded'] else 'SKIP'} {r['file']}  {r['reason']}"
              + (f"  ({len(r['warnings'])} warnings)" if r["warnings"] else ""))
    query = args.product
    if not query:
        prods = products()
        others = prods[prods["brand"].str.lower() != "danfoss"]
        if others.empty:
            print("No competitor product in the repository.")
            return 1
        query = others["product_id"].iloc[0]

    result = analyse(query, args.question, args.customer, must_haves=_must_haves(args.must) or None,
                     focus_attributes=args.focus or None, use_ai=args.live)
    if args.json:
        print(json.dumps(result.to_dict(), indent=1, ensure_ascii=False, default=str))
        return 0

    print(f"\nSTATUS: {result.status} - {result.message}")
    if result.status in ("not_found", "needs_choice"):
        for h in result.search_hits:
            print(f"  {h['score']:>3}  {h['product_id']}")
        return 0
    print(f"TARGET: {result.target['product_id']}")
    print("\nSHORTLIST (code ranking)")
    for s in result.shortlist:
        print(f"  {s['score']:>3}  {s['product_id']}  - {s['reason']}" + (f"  [{'; '.join(s['flags'])}]" if s["flags"] else ""))
    for e in result.excluded:
        print(f"  ---  {e['product_id']}  removed: {e['reason']}")
    print(f"\nCOMPARISON: target vs {result.best_match_id}")
    for r in result.comparison_rows:
        flags = f"  [{', '.join(r['flags'])}]" if r["flags"] else ""
        ai = f"  AI: {r['ai']['verdict']}" if r["ai"] else ""
        print(f"  {r['attribute']:<22} {r['target']['display']:<36} {r['candidate']['display']:<22} "
              f"{r['code_verdict']}{ai}{flags}")
    i = result.interpretation
    if i:
        print(f"\nBEST MATCH: {i['best_match']['product_id'] if i['best_match'] else 'none'} "
              f"(confidence {i['overall_confidence']})")
        print(f"  {(i['best_match'] or {}).get('why') or i.get('not_comparable_reason')}")
        for key in ("talking_points", "tradeoffs"):
            print(f"\n{key.upper().replace('_', ' ')}")
            for t in i[key]:
                print(f"  - {t['text']} [{', '.join(t['fact_ids'])}]")
    print("\nVERIFY BEFORE THE MEETING")
    for v in result.onepager["verify"]:
        f = v["fact"] or {}
        print(f"  - {f.get('id')} {f.get('product_id')} {f.get('attribute')}: {v['reason']}")
    if result.problems:
        print("\nPROBLEMS FOUND BY CODE")
        for prob in result.problems:
            print(f"  ! {prob}")
    print(f"\n(model {result.meta.get('model')}, logic {result.meta.get('logic_files')}, "
          f"cached {result.meta.get('cached')}, attempts {result.meta.get('attempts')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
