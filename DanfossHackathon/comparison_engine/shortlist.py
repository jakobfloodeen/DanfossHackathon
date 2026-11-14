"""
SHORTLIST - pick 5-10 plausible candidates, so the AI never has to read the whole repository.

    shortlist("CompetitorX|DEMO pump 71", must_haves={"rated_pressure": 250})   # Danfoss candidates
    shortlist("pvg32", pool="competitors")                                       # competitor candidates

Steps (all code, no AI):
  1. same family as the target, not the target itself, and from the chosen side:
     pool="danfoss" -> brand = Danfoss (from the LOGIC file); pool="competitors" -> every other brand
  2. customer MUST-HAVES are hard filters:
        {"rated_pressure": 250}                 -> at least 250 (in the base unit, here bar)
        {"rated_pressure": {"min": 250, "max": 300}}
        {"control_options": "LS"}               -> text must contain "LS"
     A candidate whose value is KNOWN and fails is removed. If the value is NOT STATED, the candidate
     stays, flagged "must-have not stated: ..." - the salesperson must check, we don't guess.
  3. rough size window (size_attribute +/- size_window from the LOGIC file). If too few candidates are
     inside the window, the closest ones outside are added and flagged.
  4. ranking score 0-100 = weighted closeness (weights from the LOGIC file). Unverified/conflict values
     count half. The LOGIC 'hard_filters' are only HINTS here: failing one lowers the score + adds a flag.
     The final judgement is made by the AI (interpret.py), checked by code (guardrails.py).

Returns a DataFrame (best first) with: product_id, product_name, score, compared_on, in_size_window,
flags, reason. Removed candidates are in result.attrs["excluded"] (list of {product_id, reason}).
"""
import pandas as pd

from .logic import Logic, load_logic
from .repository import best_values, load_repository
from .units import fmt

COLUMNS = ["product_id", "product_name", "score", "compared_on", "in_size_window", "flags", "reason"]
POOLS = ("danfoss", "competitors")
MIN_IN_WINDOW = 5
HINT_PENALTY = 0.8           # score multiplier per failed LOGIC hint
UNCERTAIN = {"unverified", "conflict", "calculated"}


def _number(values: pd.DataFrame, attr: str):
    if attr not in values.index:
        return None
    v = values.loc[attr, "norm_value"]
    return None if pd.isna(v) or values.loc[attr, "status"] == "missing" else float(v)


def check_must_haves(values: pd.DataFrame, must_haves: dict | None) -> tuple[list[str], list[str]]:
    """-> (failed, not_stated) lists of human-readable reasons."""
    failed, not_stated = [], []
    for attr, rule in (must_haves or {}).items():
        if isinstance(rule, str):                                       # text must-have
            text = None if attr not in values.index else values.loc[attr, "value"]
            if text is None or pd.isna(text):
                not_stated.append(attr)
            elif rule.lower() not in str(text).lower():
                failed.append(f"{attr} '{text}' does not include '{rule}'")
            continue
        lo, hi = (rule.get("min"), rule.get("max")) if isinstance(rule, dict) else (rule, None)
        v = _number(values, attr)
        if v is None:
            not_stated.append(attr)
        elif lo is not None and v < lo:
            failed.append(f"{attr} {fmt(v)} < required {fmt(lo)}")
        elif hi is not None and v > hi:
            failed.append(f"{attr} {fmt(v)} > allowed {fmt(hi)}")
    return failed, not_stated


def _hint_flags(t: pd.DataFrame, c: pd.DataFrame, logic: Logic) -> list[str]:
    """LOGIC hard_filters used as soft hints."""
    flags = []
    hf = logic.settings["hard_filters"]
    for attr, tol in (hf.get("within") or {}).items():
        tv, cv = _number(t, attr), _number(c, attr)
        if tv is not None and cv is not None and abs(cv - tv) > tol * abs(tv):
            flags.append(f"hint: {attr} {fmt(cv)} not within ±{tol:.0%} of {fmt(tv)}")
    for attr, ratio in (hf.get("at_least") or {}).items():
        tv, cv = _number(t, attr), _number(c, attr)
        if tv is not None and cv is not None and cv < ratio * tv:
            flags.append(f"hint: {attr} {fmt(cv)} below {ratio:.0%} of {fmt(tv)}")
    return flags


def closeness_score(t: pd.DataFrame, c: pd.DataFrame, weights: dict) -> tuple[float | None, list]:
    """Weighted closeness 0-100 over numeric attributes both products state (same unit)."""
    score = total = 0.0
    parts = []
    for attr, weight in weights.items():
        tv, cv = _number(t, attr), _number(c, attr)
        if not weight or tv is None or cv is None or t.loc[attr, "norm_unit"] != c.loc[attr, "norm_unit"]:
            continue
        uncertain = UNCERTAIN & {t.loc[attr, "status"], c.loc[attr, "status"]}
        eff = weight * (0.5 if uncertain else 1)
        closeness = 1 - min(abs(cv - tv) / max(abs(tv), abs(cv), 1e-9), 1)
        score, total = score + eff * closeness, total + eff
        unit = c.loc[attr, "norm_unit"] or ""
        parts.append((weight, f"{attr.replace('_', ' ')} {fmt(cv)} vs {fmt(tv)} {unit}".strip()))
    if not total:
        return None, parts
    return 100 * score / total, parts


def shortlist(target_id: str, must_haves: dict | None = None, max_size: int | None = None,
              data: pd.DataFrame | None = None, weights: dict | None = None,
              pool: str = "danfoss") -> pd.DataFrame:
    """pool = "danfoss": Danfoss candidates for a competitor product (default).
       pool = "competitors": competitor candidates for a Danfoss product (the website's flow)."""
    if pool not in POOLS:
        raise ValueError(f"pool must be one of {POOLS}")
    data = load_repository() if data is None else data
    target_rows = data[data["product_id"] == target_id]
    if target_rows.empty:
        raise KeyError(f"unknown product: {target_id}")
    family = target_rows["family"].iloc[0]
    logic = load_logic(family)
    max_size = max_size or int(logic.settings["shortlist_size"])
    t = best_values(data, target_id)
    w = {**logic.settings["weights"], **(weights or {})}
    if not any(w.values()):                     # no LOGIC weights: every numeric attribute counts the same
        w = {a: 1 for a in t.index if _number(t, a) is not None}
    size_attr, window = logic.size_attribute(), float(logic.settings["size_window"])
    brand = str(logic.settings["danfoss_brand"]).lower()

    kept, excluded = [], []
    is_danfoss = data["brand"].str.lower() == brand
    same_side = is_danfoss if pool == "danfoss" else ~is_danfoss
    candidates = data[(data["family"] == family) & same_side & (data["product_id"] != target_id)]
    for pid in candidates["product_id"].unique():
        c = best_values(data, pid)
        failed, not_stated = check_must_haves(c, must_haves)
        if failed:
            excluded.append({"product_id": pid, "reason": "; ".join(failed)})
            continue
        flags = [f"must-have not stated: {a}" for a in not_stated] + _hint_flags(t, c, logic)
        tv, cv = (_number(t, size_attr), _number(c, size_attr)) if size_attr else (None, None)
        in_window = None if tv is None or cv is None else abs(cv - tv) <= window * abs(tv)
        score, parts = closeness_score(t, c, w)
        if score is None:
            flags.append("no shared numeric values - ranked last")
            score = 0.0
        score *= HINT_PENALTY ** sum(f.startswith("hint:") for f in flags)
        reason = "; ".join(p for _, p in sorted(parts, key=lambda x: -x[0])[:3]) or "no shared numeric values"
        kept.append({"product_id": pid, "product_name": c["product_name"].iloc[0], "score": round(score),
                     "compared_on": len(parts), "in_size_window": in_window, "flags": flags, "reason": reason,
                     "_size_gap": abs(cv - tv) if in_window is not None else float("inf")})

    df = pd.DataFrame(kept, columns=COLUMNS + ["_size_gap"])
    inside = df[df["in_size_window"] != False]                       # noqa: E712 - True or unknown
    outside = df[df["in_size_window"] == False].sort_values("_size_gap")   # noqa: E712
    if len(inside) < MIN_IN_WINDOW:                                   # too few: add the closest outside
        extra = outside.head(MIN_IN_WINDOW - len(inside)).copy()
        extra["flags"] = [f + [f"outside size window (±{window:.0%} {size_attr})"] for f in extra["flags"]]
        inside = pd.concat([inside, extra])
    excluded += [{"product_id": p, "reason": f"outside size window (±{window:.0%} {size_attr})"}
                 for p in outside["product_id"] if p not in set(inside["product_id"])]
    result = (inside.sort_values(["score", "compared_on"], ascending=False).head(max_size)
              .drop(columns="_size_gap").reset_index(drop=True))
    result.attrs.update(target=target_id, family=family, excluded=excluded, logic_files=logic.files, pool=pool)
    return result
