"""
FACT SHEET - every value of the target + its candidates as numbered facts F1, F2, ...

    facts = build_facts(target_id, candidate_ids)
    diffs = code_differences(facts, logic)

A fact = ONE row of the repository: original value + unit, normalised value + unit, source file, page,
quote, status. Nothing is left out: missing values are facts too (status 'missing'), and when two sources
disagree each source becomes its own fact (status 'conflict'), so the AI and the user see ALL values.

The AI may only talk about these facts and must cite their ids. Code-computed differences (Diff) are
only made where code can be sure: same attribute, same unit, one clear number on each side.
"""
from dataclasses import asdict, dataclass, field

import pandas as pd

from . import config
from .logic import Logic
from .repository import load_repository
from .units import fmt


@dataclass
class Fact:
    id: str                     # "F7"
    product_id: str
    role: str                   # "target" or "candidate"
    attribute: str
    value: str | None           # as written in the source
    unit: str | None
    norm_value: float | None    # in the base unit (bar, l/min, cc/rev, rpm, kg, mm), None for text
    norm_unit: str | None
    source_file: str | None     # "calculated" for values our code computed
    page: int | None
    quote: str | None
    status: str
    csv_file: str | None = None
    note: str = ""
    based_on: list[str] = field(default_factory=list)   # fact ids a calculated value was made from
    attribute_original: str = ""                         # the datasheet's own name, if it was renamed

    def display(self) -> str:
        """'71 cc/rev', 'PC, LS' or 'not stated'."""
        if self.status == "missing" or self.value is None:
            return "not stated"
        if self.norm_value is not None:
            return f"{fmt(self.norm_value)} {self.norm_unit or ''}".strip()
        return f"{self.value} {self.unit or ''}".strip()

    def source(self) -> str:
        if self.source_file == "calculated":
            return "calculated: " + (self.quote or "")
        return f"{self.source_file or '?'}" + (f" p.{self.page}" if self.page else "")

    def is_number(self) -> bool:
        return self.norm_value is not None and self.status not in ("missing", "unknown unit")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(display=self.display(), source=self.source())
        return d


@dataclass
class Diff:
    """A difference computed by CODE (never by the AI): candidate vs target, same attribute + unit."""
    attribute: str
    candidate: str
    target_fact: str
    candidate_fact: str
    target_value: float
    candidate_value: float
    unit: str
    diff_pct: float             # (candidate - target) / target * 100
    candidate_value_is: str     # "higher" / "lower" / "similar"
    better: str | None          # "candidate" / "target" / "similar" / None (LOGIC gives no direction)

    def to_dict(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        b = f"; better: {self.better}" if self.better else ""
        return (f"{self.attribute}: candidate {fmt(self.candidate_value)} vs target {fmt(self.target_value)} "
                f"{self.unit} = {self.diff_pct:+.1f}% ({self.candidate_value_is}{b})")


def _clean(v):
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def _attribute_order(rows: pd.DataFrame, logic: Logic | None) -> list[str]:
    weighted = list((logic.settings["weights"] if logic else {}).keys())
    rest = sorted(set(rows["attribute"]) - set(weighted))
    return [a for a in weighted if a in set(rows["attribute"])] + rest


def build_facts(target_id: str, candidate_ids: list[str], data: pd.DataFrame | None = None,
                logic: Logic | None = None) -> list[Fact]:
    """Numbered facts: the target first, then each candidate in the given order.
    Within a product: LOGIC weight order, then alphabetical; all rows of an attribute (conflicts!)."""
    data = load_repository() if data is None else data
    facts: list[Fact] = []
    for pid in [target_id] + [c for c in candidate_ids if c != target_id]:
        rows = data[data["product_id"] == pid].copy()
        rows["_rank"] = rows["status"].map(config.STATUS_RANK).fillna(9)
        by_attr: dict[str, list[str]] = {}
        product_facts = []
        for attr in _attribute_order(rows, logic):
            for _, r in rows[rows["attribute"] == attr].sort_values(["_rank", "source_file"]).iterrows():
                f = Fact(id=f"F{len(facts) + len(product_facts) + 1}", product_id=pid,
                         role="target" if pid == target_id else "candidate", attribute=attr,
                         value=_clean(r["value"]), unit=_clean(r["unit"]), norm_value=_clean(r["norm_value"]),
                         norm_unit=_clean(r["norm_unit"]), source_file=_clean(r["source_file"]),
                         page=None if _clean(r["page"]) is None else int(r["page"]), quote=_clean(r["quote"]),
                         status=r["status"], csv_file=_clean(r["csv_file"]), note=r.get("note") or "",
                         attribute_original=(_clean(r.get("attribute_original")) or "")
                         if _clean(r.get("attribute_original")) != attr else "",
                         based_on=list(r["based_on"]) if isinstance(r.get("based_on"), list) else [])
                product_facts.append(f)
                by_attr.setdefault(attr, []).append(f.id)
        for f in product_facts:          # calculated values: attribute names -> fact ids of the inputs
            if f.status == "calculated":
                f.based_on = [fid for a in f.based_on for fid in by_attr.get(a, [])]
        facts += product_facts
    return facts


def facts_by_id(facts: list[Fact]) -> dict[str, Fact]:
    return {f.id: f for f in facts}


def compare_numbers(target_value: float, candidate_value: float, similar_within: float) -> tuple[float, str]:
    """-> (diff in %, 'higher'/'lower'/'similar') for candidate vs target."""
    diff = (candidate_value - target_value) / max(abs(target_value), 1e-9)
    if abs(diff) <= similar_within:
        return diff * 100, "similar"
    return diff * 100, "higher" if diff > 0 else "lower"


def code_differences(facts: list[Fact], logic: Logic) -> list[Diff]:
    """For each candidate: every attribute where target and candidate have exactly ONE usable number
    in the same unit. Conflicts are skipped on purpose (code can't know which value is right)."""
    similar = float(logic.settings["similar_within"])
    better_dir = logic.settings["better"]
    numeric: dict[tuple[str, str], list[Fact]] = {}
    for f in facts:
        if f.status != "missing":
            numeric.setdefault((f.product_id, f.attribute), []).append(f)

    target = next((f.product_id for f in facts if f.role == "target"), None)
    candidates = list(dict.fromkeys(f.product_id for f in facts if f.role == "candidate"))
    diffs = []
    for cand in candidates:
        for (pid, attr), t_list in numeric.items():
            if pid != target:
                continue
            c_list = numeric.get((cand, attr), [])
            if len(t_list) != 1 or len(c_list) != 1:
                continue
            t, c = t_list[0], c_list[0]
            if not (t.is_number() and c.is_number()) or t.norm_unit != c.norm_unit or "conflict" in (t.status, c.status):
                continue
            pct, word = compare_numbers(t.norm_value, c.norm_value, similar)
            direction = better_dir.get(attr)
            if word == "similar":
                better = "similar" if direction else None
            elif direction in ("higher", "lower"):
                better = "candidate" if (word == direction) else "target"
            else:
                better = None
            diffs.append(Diff(attribute=attr, candidate=cand, target_fact=t.id, candidate_fact=c.id,
                              target_value=t.norm_value, candidate_value=c.norm_value, unit=t.norm_unit or "",
                              diff_pct=round(pct, 1), candidate_value_is=word, better=better))
    return diffs
