"""
Units, conflicts, comparison and matching (owner: P3). Pure pandas - no AI in here.
"""
import re

import pandas as pd

import db
from schema import attributes, numeric_attributes

# (from_unit, to_unit) -> factor. Units are lower-cased and cleaned before lookup.
CONVERSIONS = {
    ("psi", "bar"): 0.0689476, ("mpa", "bar"): 10.0, ("bar", "bar"): 1.0,
    ("gpm", "l/min"): 3.78541, ("us gpm", "l/min"): 3.78541, ("l/min", "l/min"): 1.0,
    ("in3/rev", "cc/rev"): 16.3871, ("in³/rev", "cc/rev"): 16.3871,
    ("cm3/rev", "cc/rev"): 1.0, ("cm³/rev", "cc/rev"): 1.0, ("cm3", "cc/rev"): 1.0,
    ("cm³", "cc/rev"): 1.0, ("cc/rev", "cc/rev"): 1.0, ("cc", "cc/rev"): 1.0,
    ("rpm", "rpm"): 1.0, ("min-1", "rpm"): 1.0, ("1/min", "rpm"): 1.0, ("min⁻¹", "rpm"): 1.0,
    ("kg", "kg"): 1.0, ("lb", "kg"): 0.453592, ("lbs", "kg"): 0.453592,
    ("count", "count"): 1.0, ("", "count"): 1.0, ("sections", "count"): 1.0,
}


def to_number(value):
    """'350 bar' -> 350.0 ; '1,450' -> 1450.0 ; 'n/a' -> None"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    m = re.search(r"-?\d[\d,]*\.?\d*", str(value))
    return float(m.group().replace(",", "")) if m else None


def normalize(value, unit, target_unit):
    """Returns the value in our standard unit, or None if we can't convert safely."""
    num = to_number(value)
    if num is None or target_unit is None:
        return None
    key = (str(unit or "").strip().lower().replace(" ", ""), target_unit)
    key = (key[0].replace("usgpm", "us gpm"), key[1])
    factor = CONVERSIONS.get(key)
    return round(num * factor, 2) if factor is not None else None


# ---------------------------------------------------------------- LOAD
def load_products() -> pd.DataFrame:
    """All product values from the library database, with units normalised + flags."""
    df = db.load_attributes()
    if df.empty:
        return df
    df["norm_value"] = [
        normalize(v, u, attributes(fam).get(a, {}).get("unit"))
        for v, u, fam, a in zip(df["value"], df["unit"], df["family"], df["attribute"])
    ]
    df = add_calculated(df)
    return flag_conflicts(df)


def add_calculated(df: pd.DataFrame) -> pd.DataFrame:
    """Pumps: max_flow = displacement x speed / 1000, if the datasheet doesn't state it."""
    new_rows = []
    for pid, g in df[df["family"] == "pump"].groupby("product_id"):
        vals = g.groupby("attribute")["norm_value"].first()  # first non-empty value per attribute
        has_flow = "max_flow" in vals and pd.notna(vals.get("max_flow"))
        if not has_flow and pd.notna(vals.get("max_displacement")) and pd.notna(vals.get("max_speed")):
            flow = round(vals["max_displacement"] * vals["max_speed"] / 1000, 1)
            base = g.iloc[0].to_dict()
            base.update(attribute="max_flow", value=flow, unit="l/min", norm_value=flow,
                        page=None, quote="calculated: displacement x speed / 1000",
                        status="calculated", source_file="calculated", doc_id=None, id=None)
            new_rows.append(base)
    if new_rows:
        df = df[~((df["attribute"] == "max_flow") & df["norm_value"].isna()
                  & df["product_id"].isin([r["product_id"] for r in new_rows]))]
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df


def flag_conflicts(df: pd.DataFrame, tolerance: float = 0.02) -> pd.DataFrame:
    """Same product + attribute from 2+ sources with values >2% apart -> 'conflict'."""
    for (pid, attr), g in df.groupby(["product_id", "attribute"]):
        nums = g["norm_value"].dropna()
        if len(nums) > 1 and (nums.max() - nums.min()) / max(abs(nums.max()), 1e-9) > tolerance:
            df.loc[nums.index, "status"] = "conflict"
    return df


# ---------------------------------------------------------------- COMPARE
def _best_row(g: pd.DataFrame):
    """If several sources exist, prefer verified ones."""
    order = {"manual": 0, "ok": 0, "calculated": 1, "conflict": 2, "unverified": 3, "missing": 4}
    return g.assign(_o=g["status"].map(order).fillna(9)).sort_values("_o").iloc[0]


def compare(df: pd.DataFrame, id_a: str, id_b: str) -> pd.DataFrame:
    """Side-by-side table for two products. A = Danfoss, B = competitor (by convention)."""
    family = df.loc[df["product_id"] == id_a, "family"].iloc[0]
    rows = []
    for i, (attr, spec) in enumerate(attributes(family).items(), start=1):
        ra = df[(df["product_id"] == id_a) & (df["attribute"] == attr)]
        rb = df[(df["product_id"] == id_b) & (df["attribute"] == attr)]
        a = _best_row(ra) if len(ra) else None
        b = _best_row(rb) if len(rb) else None
        va = a["norm_value"] if a is not None else None
        vb = b["norm_value"] if b is not None else None

        if a is None or b is None or a["status"] == "missing" or b["status"] == "missing":
            verdict = "missing data"
        elif spec["numeric"] and pd.notna(va) and pd.notna(vb):
            diff = (va - vb) / max(abs(vb), 1e-9)
            if abs(diff) <= 0.03 or spec["better"] is None:
                verdict = "similar" if abs(diff) <= 0.03 else f"{diff:+.0%}"
            else:
                a_wins = (diff > 0) == (spec["better"] == "higher")
                verdict = f"{'Danfoss' if a_wins else 'Competitor'} better ({diff:+.0%})"
        else:
            verdict = "compare text"

        def show(r, v):
            if r is None or r["status"] == "missing":
                return "—"
            return f"{v:g} {spec['unit']}" if spec["numeric"] and pd.notna(v) else str(r["value"])

        def src(r):
            if r is None or r["status"] == "missing":
                return ""
            if r["status"] == "calculated":
                return "calculated"
            return f"{r['source_file']} p.{int(r['page'])}" if pd.notna(r["page"]) else str(r["source_file"])

        rows.append({
            "id": f"R{i}", "attribute": attr,
            "danfoss": show(a, va), "competitor": show(b, vb), "verdict": verdict,
            "danfoss_source": src(a), "competitor_source": src(b),
            "danfoss_status": a["status"] if a is not None else "missing",
            "competitor_status": b["status"] if b is not None else "missing",
            "danfoss_link": db.doc_link(a["source_file"], a["page"]) if a is not None and a["status"] != "missing" else None,
            "competitor_link": db.doc_link(b["source_file"], b["page"]) if b is not None and b["status"] != "missing" else None,
            "danfoss_quote": a["quote"] if a is not None else None,
            "competitor_quote": b["quote"] if b is not None else None,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- MATCH
def match(df: pd.DataFrame, target_id: str, weights: dict[str, float],
          constraints: dict[str, float] | None = None, brand: str = "Danfoss", top: int = 3):
    """
    Find the closest `brand` products to target_id.
      weights:     {attribute: importance 0..5}   (selected features)
      constraints: {attribute: minimum value}     (customer must-haves = hard filter)
    Score = weighted closeness on numeric attributes, 0..100.
    """
    constraints = constraints or {}
    target = df[df["product_id"] == target_id]
    family = target["family"].iloc[0]
    tvals = target.groupby("attribute")["norm_value"].first()

    results = []
    candidates = df[(df["brand"] == brand) & (df["family"] == family)]  # hard filter: same type
    for pid, g in candidates.groupby("product_id"):
        cvals = g.groupby("attribute")["norm_value"].first()
        if any(pd.isna(cvals.get(a)) or cvals.get(a) < v for a, v in constraints.items()):
            continue  # fails a customer must-have
        score = wsum = 0.0
        compared = []
        for attr in numeric_attributes(family):
            w = weights.get(attr, 0)
            t, c = tvals.get(attr), cvals.get(attr)
            if w <= 0 or pd.isna(t) or pd.isna(c):
                continue
            closeness = 1 - min(abs(c - t) / max(abs(t), abs(c), 1e-9), 1)
            score += w * closeness
            wsum += w
            compared.append(attr)
        if wsum:
            results.append({"product_id": pid, "score": round(100 * score / wsum),
                            "attributes_compared": len(compared)})
    return pd.DataFrame(results).sort_values("score", ascending=False).head(top) if results \
        else pd.DataFrame(columns=["product_id", "score", "attributes_compared"])
