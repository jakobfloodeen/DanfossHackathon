"""
SYSTEMS - compare a set of components (e.g. pump + valve) with another set.

    danfoss    = System("system:danfoss", "Danfoss system", "Danfoss", {"pump": "...pump 75", "valve": "pvg32"})
    competitor = System("system:competitor", "Competitor system", "Competitors", {"pump": "...", "valve": "kawasaki-kmp"})
    result = compare_systems(danfoss, competitor, user_question, customer_context)

How it works (so the rest of the engine needs no changes):
  1. each system becomes ONE virtual product; its facts are the component facts with the component in
     front of the name: pump__max_flow, valve__max_inlet_flow (source, page, quote, status unchanged)
  2. CODE adds system values from the rules in rules/system.md, e.g.
        system__max_flow = min(pump.max_flow, valve.max_inlet_flow)          -> status "calculated"
     If an input is missing / in conflict / text / wrong unit -> "cannot calculate: <reason>" (status missing)
  3. the normal interpret() compares the two virtual products: facts F1.., one AI call, guardrails, table.
     Fact ids cite the component facts, so every system value still links back to its datasheet pages.

compose_system() fills a component automatically (best shortlist match) when the user leaves it open.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import commercial, config
from .interpret import interpret
from .logic import load_logic
from .repository import load_repository
from .results import AnalysisResult
from .shortlist import shortlist
from .units import fmt

# min / max / sum take 2 OR MORE inputs and use the components the system actually has
# (a pressure chain over pump + valve + motor still works when the system has no motor).
# difference / ratio take exactly 2. A rule may also set:
#   scale: multiply the result (e.g. flow / displacement needs x1000 to become rpm)
#   unit:  the unit of the result when it is not the inputs' unit (e.g. rpm)
NARY_OPS = {"min": min, "max": max, "sum": sum}
PAIR_OPS = {"difference": lambda a, b: a - b, "ratio": lambda a, b: a / b if b else None}
SEP = "__"


@dataclass
class System:
    id: str                                   # "system:danfoss"
    name: str                                 # shown as the column title
    brand: str
    components: dict[str, str | None]         # role -> product_id (None = not chosen / no match)
    notes: list[str] = field(default_factory=list)   # e.g. "valve chosen automatically (score 64)"


def _component_rows(system: System, data: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for role, pid in system.components.items():
        rows = data[data["product_id"] == pid].copy() if pid else pd.DataFrame()
        if rows.empty:
            continue
        rows["attribute"] = role + SEP + rows["attribute"]
        rows["attribute_original"] = rows["attribute_original"].fillna("")
        rows["based_on"] = [[role + SEP + b for b in (x if isinstance(x, list) else [])] for x in rows["based_on"]]
        rows["note"] = (f"{role}: {pid}; " + rows["note"].fillna("")).str.rstrip("; ")
        rows["product_id"], rows["brand"], rows["product_name"], rows["family"] = system.id, system.brand, system.name, "system"
        parts.append(rows)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=data.columns)


def _best(rows: pd.DataFrame, attribute: str) -> tuple[pd.Series | None, str]:
    """The value a rule may use, or (None, reason)."""
    r = rows[rows["attribute"] == attribute]
    if r.empty or r["status"].eq("missing").all():
        return None, f"{attribute.replace(SEP, '.')} not stated"
    if r["status"].eq("conflict").any():
        return None, f"{attribute.replace(SEP, '.')} is in conflict"
    r = r[r["norm_value"].notna()]
    if r.empty:
        return None, f"{attribute.replace(SEP, '.')} is not a number"
    return r.sort_values("status", key=lambda s: s.map(config.STATUS_RANK)).iloc[0], ""


def system_values(system: System, rows: pd.DataFrame, rules: list[dict]) -> pd.DataFrame:
    """Rows for the system values (calculated, or 'cannot calculate' with the reason).
    A rule that names a component the system does not have is skipped silently: min/max/sum drop the
    absent components and go on when at least two remain, difference/ratio need both."""
    present = {role for role, pid in system.components.items() if pid}
    out = []
    for rule in rules:
        op, inputs = rule.get("op"), [str(i).replace(".", SEP, 1) for i in rule.get("inputs", [])]
        if (op not in NARY_OPS and op not in PAIR_OPS) or len(inputs) < 2:
            continue                                            # a broken rule is skipped, never guessed
        inputs = [a for a in inputs if a.split(SEP, 1)[0] in present]
        if (op in PAIR_OPS and len(inputs) != len(rule.get("inputs", []))) or len(inputs) < 2:
            continue                                            # the system does not have these components
        if op in PAIR_OPS and len(inputs) != 2:
            continue
        picked = [_best(rows, a) for a in inputs]
        reasons = [why for value, why in picked if value is None]
        units = {v["norm_unit"] for v, _ in picked if v is not None}
        scale = float(rule.get("scale", 1) or 1)
        if not reasons and op != "ratio" and len(units) > 1:
            reasons.append(f"units differ ({', '.join(sorted(units))})")
        formula = f"{op}({', '.join(i.replace(SEP, '.') for i in inputs)})" + (f" x {fmt(scale)}" if scale != 1 else "")
        base = {"product_id": system.id, "brand": system.brand, "product_name": system.name, "family": "system",
                "attribute": "system" + SEP + rule["name"], "attribute_original": "", "source_file": "calculated",
                "page": None, "csv_file": None, "csv_line": None, "based_on": inputs,
                "note": rule.get("explain", "")}
        if reasons:
            out.append({**base, "value": None, "unit": None, "norm_value": None, "norm_unit": None,
                        "quote": f"{formula} - cannot calculate: {'; '.join(reasons)}", "status": "missing"})
            continue
        values = [float(v["norm_value"]) for v, _ in picked]
        value = NARY_OPS[op](values) if op in NARY_OPS else PAIR_OPS[op](*values)
        if value is None:
            continue
        value *= scale
        unit = str(rule.get("unit", "" if op == "ratio" else next(iter(units))))
        value = round(value, 2 if op == "ratio" and scale == 1 else 1)
        out.append({**base, "value": fmt(value), "unit": unit, "norm_value": value, "norm_unit": unit,
                    "quote": f"{formula} = {fmt(value)} {unit}".strip(), "status": "calculated"})
    return pd.DataFrame(out)


def build_system_data(systems: list[System], data: pd.DataFrame | None = None) -> pd.DataFrame:
    """The repository rows of all systems as virtual products (component rows + system values)."""
    data = load_repository() if data is None else data
    rules = load_logic("system").settings.get("system_rules") or []
    frames = []
    for s in systems:
        rows = _component_rows(s, data)
        frames += [rows, system_values(s, rows, rules)]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=data.columns)


def compose_system(system_id: str, name: str, brand: str, fixed: dict[str, str | None],
                   anchor: System, pool: str, data: pd.DataFrame | None = None) -> System:
    """Fill open components automatically: for each role the best shortlist match of the anchor's component."""
    data = load_repository() if data is None else data
    components, notes = {}, []
    for role, pid in fixed.items():
        if pid:
            components[role] = pid
            continue
        anchor_pid = anchor.components.get(role)
        if not anchor_pid or anchor_pid not in set(data["product_id"]):
            components[role] = None
            notes.append(f"{role}: no comparable product (nothing to match against)")
            continue
        s = shortlist(anchor_pid, data=data, pool=pool)
        components[role] = s.iloc[0]["product_id"] if not s.empty else None
        notes.append(f"{role}: chosen automatically (code ranking score {s.iloc[0]['score']})" if not s.empty
                     else f"{role}: no {'Danfoss' if pool == 'danfoss' else 'competitor'} product with data")
    return System(system_id, name, brand, components, notes)


def compare_systems(anchor: System, other: System, user_question: str = "", customer_context: str = "",
                    ask=None, use_ai: bool = True, customer_facts=None, pool: str = "competitors",
                    profile_facts=None, priorities=None) -> AnalysisResult:
    """One comparison of two systems with the normal engine (facts, one AI call, guardrails)."""
    from . import customer_profile
    data = build_system_data([anchor, other])
    candidates = [other.id] if (data["product_id"] == other.id).any() else []
    component_ids = [pid for s in (anchor, other) for pid in s.components.values() if pid]
    terms, regions = customer_profile.terms_from(list(profile_facts or []), f"{customer_context} {user_question}")
    names = [pid.split("|", 1)[-1] for pid in component_ids]
    shown, other_notes = commercial.relevant_notes("", component_ids, "system", names, terms, regions,
                                                   kinds=commercial.kinds_for(priorities))
    notes = commercial.facts(shown)
    for f, r in zip(notes, shown):
        f.condition = r["why"]
    result = interpret(anchor.id, candidates, user_question, customer_context, ask=ask, use_ai=use_ai,
                       data=data, pool="system" if pool == "competitors" else "system_reverse",
                       customer_facts=customer_facts, commercial_notes=notes, profile_facts=profile_facts,
                       priorities=priorities)
    result.meta["commercial_other"] = other_notes
    result.meta["systems"] = {s.id: {"name": s.name, "components": s.components, "notes": s.notes}
                              for s in (anchor, other)}
    return result
