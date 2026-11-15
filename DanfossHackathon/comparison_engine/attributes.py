"""
ATTRIBUTES - recognise the same quantity under different datasheet names, in code (no AI, no cost).

Every datasheet names things its own way: "ambient_temperature" vs "ambient_temperature_range",
"oil_temperature_max" vs "max_hydraulic_fluid_temperature". Two names are treated as ONE attribute when
they have the same KEY:

    1. split into words:           max_hydraulic_fluid_temperature -> max, hydraulic, fluid, temperature
    2. same-meaning words unified: hydraulic fluid -> oil, maximum -> max, temp -> temperature ...
    3. filler words dropped:       range, permissible, allowable, iso4406 ...
    4. word order ignored:         {max, oil, temperature}  (unless the name has a direction like "a_to_b")

The vocabulary lives in rules/_general.md (attribute_words), so the product team can extend it.
Then a range is split when it helps: "-35 to +110" for a max/min-style name, or a range whose other
products give separate min/max values, also becomes a ..._min (-35) and a ..._max (110) fact - same page,
same quote, marked "from the range ...", so it lines up with sheets that state min and max separately.

The datasheet's own name stays visible as attribute_original ("datasheet calls it ...").
"""
import re

import pandas as pd

from .logic import load_logic

DEFAULT_SAME = {                          # phrase -> word (phrases with "_" are matched first)
    "hydraulic_fluid": "oil", "hydraulic_oil": "oil", "fluid": "oil", "medium": "oil",
    "maximum": "max", "maximal": "max", "minimum": "min", "minimal": "min", "temp": "temperature",
    "continous": "continuous", "cont": "continuous", "mass": "weight", "contamination": "cleanliness",
    "filtration": "cleanliness", "no": "number", "nr": "number", "qty": "number",
}
DEFAULT_IGNORE = ("range", "permissible", "permitted", "allowable", "admissible", "allowed", "value", "values",
                  "level", "the", "of", "for", "iso4406", "iso", "4406", "class", "specified")
DIRECTION = {"to", "from", "vs", "versus", "into"}
MAXMIN = {"max", "min"}
_RANGE = re.compile(r"^\s*([+-]?\d[\d.,]*)\s*(?:to|–|—|→|\.\.\.?|…|\s-\s|-(?=\s*[+]?\d))\s*([+-]?\d[\d.,]*)\s*$", re.I)


def _vocabulary(family: str) -> tuple[dict, set]:
    words = load_logic(family).settings.get("attribute_words") or {}
    same = {**DEFAULT_SAME, **{str(k).lower(): str(v).lower() for k, v in (words.get("same") or {}).items()}}
    ignore = set(DEFAULT_IGNORE) | {str(w).lower() for w in (words.get("ignore") or [])}
    return same, ignore


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def key(attribute: str, same: dict, ignore: set) -> tuple:
    """The matching key of an attribute name (see the module text)."""
    s = "_" + str(attribute).lower().strip("_") + "_"
    for phrase in sorted((p for p in same if "_" in p), key=len, reverse=True):
        s = s.replace("_" + phrase + "_", "_" + same[phrase] + "_")
    words = [same.get(w, w) for w in s.strip("_").split("_") if w]
    words = [_singular(w) for w in words if w not in ignore]
    words = [w for i, w in enumerate(words) if w not in words[:i]]              # "oil_oil" -> "oil"
    return tuple(words) if DIRECTION & set(words) else tuple(sorted(words))


def parse_range(value) -> tuple[str, str] | None:
    """'-35 to +110' -> ('-35', '110'); '12 - 75' -> ('12', '75'); a single number -> None."""
    m = _RANGE.match(str(value or ""))
    if not m:
        return None
    lo, hi = m.group(1).lstrip("+"), m.group(2).lstrip("+")
    return (lo, hi)


def _representative(names: list[str], counts: dict, known: set) -> str:
    """The name a group is shown under: a standard name from the rules first, then the most used one."""
    return sorted(names, key=lambda n: (n not in known, -counts.get(n, 0), len(n), n))[0]


def unify(data: pd.DataFrame) -> pd.DataFrame:
    """Rename every attribute to its group's name (per family) and split ranges where that lines them up."""
    if data.empty:
        return data
    out = []
    for family, fam in data.groupby("family", sort=False):
        same, ignore = _vocabulary(family or "_general")
        settings = load_logic(family or "_general").settings
        known = set(settings.get("weights", {})) | set(settings.get("better", {})) | \
            set((settings.get("attribute_aliases") or {}).values())
        keys = {a: key(a, same, ignore) for a in fam["attribute"].unique()}

        # a RANGE value under a max/min-style name describes the whole range: file it under the plain key
        is_range = fam["value"].map(lambda v: parse_range(v) is not None)
        row_key = [tuple(w for w in keys[a] if w not in MAXMIN) if r and MAXMIN & set(keys[a]) else keys[a]
                   for a, r in zip(fam["attribute"], is_range)]
        fam = fam.assign(_key=row_key)

        # split a range into min/max when another fact uses that min/max key
        scalar_keys = set(fam.loc[~is_range, "_key"])
        extra = []
        for idx in fam.index[is_range]:
            row = fam.loc[idx]
            lo, hi = parse_range(row["value"])
            for end, val in (("min", lo), ("max", hi)):
                k = tuple(sorted(set(row["_key"]) | {end})) if not DIRECTION & set(row["_key"]) else row["_key"] + (end,)
                if k in scalar_keys:
                    new = row.to_dict()
                    new.update(value=val, _key=k, _derived=f"{end} end of the stated range {row['value']}")
                    extra.append(new)
        if extra:
            fam = pd.concat([fam, pd.DataFrame(extra)], ignore_index=True)

        counts: dict = {}                                        # how often each name is used IN its group
        for a, k in zip(fam["attribute"], fam["_key"]):
            counts.setdefault(k, {}).setdefault(a, 0)
            counts[k][a] += 1
        name = {}
        for k, used in counts.items():
            ends = MAXMIN & set(k)
            plain = [n for n in used if bool(MAXMIN & set(keys.get(n, ()))) == bool(ends)]
            if plain:
                chosen = _representative(plain, used, known)
            else:
                chosen = _representative(sorted(used), used, known)
                words = [w for w in chosen.split("_") if w not in ("max", "min", "maximum", "minimum")]
                chosen = "_".join(words) if not ends else "_".join(words) + "_" + ends.pop()
            name[k] = chosen
        fam["attribute"] = [name[k] for k in fam["_key"]]
        out.append(fam)
    data = pd.concat(out, ignore_index=True)
    if "_derived" not in data.columns:
        data["_derived"] = None
    return data.drop(columns=["_key"])
