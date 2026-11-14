"""
REPOSITORY - all product CSVs in one folder, loaded as ONE clean table.

    load_repository()          -> pandas DataFrame, one row per (product, attribute, source)
    repository_report()        -> which files were loaded / skipped, and why (maintenance view)
    validate_csv(df)           -> list of problems; the PDF->CSV converter calls this BEFORE saving
    validate_csv_file(path)    -> same, for a file on disk
    products()                 -> one row per product

What happens when loading:
  1. every *.csv in the folder is read; broken files are skipped and reported (never crash the website)
  2. CSV headers are renamed with config.COLUMNS (the ONE place that knows the agreed format)
  3. original value + unit are kept; norm_value + norm_unit are added (units.py)
  4. statuses are checked: no page/quote -> 'unverified', no value -> 'missing', strange unit -> 'unknown unit'
  5. calculated values are added (e.g. pump flow = displacement x speed / 1000), marked 'calculated'
  6. conflicts: same product + attribute from 2+ sources that disagree -> ALL rows get 'conflict'
The table is cached and re-read automatically when a CSV is added, changed or removed (fingerprint).
"""
from pathlib import Path

import pandas as pd

from . import config
from .logic import canonical_family, load_logic
from .units import is_empty, is_known_unit, normalize, parse_number

OUR_COLUMNS = list(config.COLUMNS)
EXTRA_COLUMNS = ["norm_value", "norm_unit", "csv_file", "csv_line", "note", "based_on"]

# Calculated values. Only computed when the source does NOT state the value itself,
# and only from inputs that are not in conflict / missing / unknown unit.
CALCULATIONS = [
    {"family": "pump", "attribute": "max_flow", "inputs": ["max_displacement", "max_speed"],
     "unit": "l/min", "formula": "max_displacement x max_speed / 1000",
     "compute": lambda displacement, speed: displacement * speed / 1000},
]

_CACHE: dict = {}   # repo folder -> {"fingerprint", "data", "report"}


# ---------------------------------------------------------------- reading + validating one file
def read_csv_file(path: Path) -> pd.DataFrame:
    """Read a CSV as text (never let pandas guess numbers). Handles UTF-8 BOM, Latin-1 and ';' files."""
    last_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], encoding=encoding)
            if len(df.columns) == 1 and ";" in df.columns[0]:          # Excel in Europe saves with ';'
                df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""],
                                 encoding=encoding, sep=";")
            df.columns = [str(c).strip() for c in df.columns]
            return df
        except UnicodeDecodeError as e:
            last_error = e
    raise last_error


def _rename_to_ours(df: pd.DataFrame) -> pd.DataFrame:
    inverse = {csv_name: ours for ours, csv_name in config.COLUMNS.items()}
    return df.rename(columns=inverse)


def validate_csv(df: pd.DataFrame) -> list[str]:
    """Check a CSV (as read from disk, with the agreed headers) BEFORE saving it to the repository.
    Returns a list of problems in plain English; an empty list = the file is fine."""
    if df is None or len(df.columns) == 0:
        return ["file is empty"]
    df = _rename_to_ours(df.copy())
    missing = [config.COLUMNS[c] for c in config.REQUIRED if c not in df.columns]
    if missing:
        return [f"missing columns: {', '.join(missing)}"]
    if df.empty:
        return ["file has a header but no rows"]

    problems = []
    for i, row in df.iterrows():
        line = f"line {i + 2}"                                  # +2: header is line 1
        for col in ("brand", "product_name", "family", "attribute"):
            if is_empty(row.get(col)):
                problems.append(f"{line}: '{config.COLUMNS[col]}' is empty")
        status = "" if is_empty(row.get("status")) else str(row["status"]).strip().lower()
        if status and status not in config.CSV_STATUSES:
            problems.append(f"{line}: status '{status}' is not one of {', '.join(config.CSV_STATUSES)}")
        has_value = not is_empty(row.get("value"))
        if not has_value and status not in ("", "missing"):
            problems.append(f"{line}: no value but status is '{status}' (use 'missing')")
        if has_value and status == "missing":
            problems.append(f"{line}: has a value but status is 'missing'")
        if has_value and status in config.TRUSTED and (is_empty(row.get("page")) or is_empty(row.get("quote"))):
            problems.append(f"{line}: status '{status}' needs a page and a quote (source traceability)")
        if not is_empty(row.get("page")) and parse_number(row["page"]) is None:
            problems.append(f"{line}: page '{row['page']}' is not a number")
        if has_value and not is_empty(row.get("unit")) and not is_known_unit(row["unit"]):
            problems.append(f"{line}: unknown unit '{row['unit']}' (value can't be compared)")
    return problems


def validate_csv_file(path: str | Path) -> list[str]:
    try:
        return validate_csv(read_csv_file(Path(path)))
    except Exception as e:  # noqa: BLE001 - any read error is reported, never raised
        return [f"unreadable: {e}"]


# ---------------------------------------------------------------- loading the whole folder
def _csv_files(repo: Path) -> list[Path]:
    if not repo.exists():
        return []
    return sorted(p for p in repo.rglob("*.csv") if not p.name.startswith("."))


def _fingerprint(repo: Path, files: list[Path]) -> tuple:
    return str(repo.resolve()), tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)


def load_repository(repo_dir: str | Path | None = None) -> pd.DataFrame:
    """All CSVs as one clean table. Cached; re-read automatically when files change."""
    repo = Path(repo_dir) if repo_dir else config.repo_dir()
    files = _csv_files(repo)
    fp = _fingerprint(repo, files)
    cached = _CACHE.get(fp[0])
    if cached and cached["fingerprint"] == fp:
        return cached["data"]

    frames, report = [], []
    for f in files:
        entry = {"file": f.name, "rows": 0, "loaded": False, "reason": "", "warnings": []}
        try:
            raw = read_csv_file(f)
            entry["rows"] = len(raw)
            problems = validate_csv(raw)
            fatal = [p for p in problems if not p.startswith("line ")]
            if fatal:
                entry["reason"] = "; ".join(fatal)
            else:
                df = _rename_to_ours(raw)
                df["csv_file"] = f.name
                df["csv_line"] = range(2, len(df) + 2)
                frames.append(df)
                entry.update(loaded=True, warnings=problems)
        except Exception as e:  # noqa: BLE001 - a broken file must never crash the website
            entry["reason"] = f"unreadable: {e}"
        report.append(entry)

    data = _clean(pd.concat(frames, ignore_index=True)) if frames else _empty_table()
    _CACHE[fp[0]] = {"fingerprint": fp, "data": data,
                     "report": pd.DataFrame(report, columns=["file", "rows", "loaded", "reason", "warnings"])}
    return data


def repository_report(repo_dir: str | Path | None = None) -> pd.DataFrame:
    """One row per CSV file: loaded yes/no, why not, and row-level warnings."""
    repo = Path(repo_dir) if repo_dir else config.repo_dir()
    load_repository(repo)
    return _CACHE[str(repo.resolve())]["report"]


def _empty_table() -> pd.DataFrame:
    return pd.DataFrame(columns=OUR_COLUMNS + EXTRA_COLUMNS)


def _text(series: pd.Series) -> pd.Series:
    return series.map(lambda v: None if is_empty(v) else " ".join(str(v).split()))


def _clean(data: pd.DataFrame) -> pd.DataFrame:
    for col in OUR_COLUMNS:
        if col not in data.columns:
            data[col] = None
    for col in OUR_COLUMNS:
        data[col] = _text(data[col])
    data["brand"] = data["brand"].fillna("")
    data["product_name"] = data["product_name"].fillna("")
    data["product_id"] = data["product_id"].fillna(data["brand"] + "|" + data["product_name"])
    data["attribute"] = data["attribute"].fillna("").str.lower().str.replace(" ", "_")
    families = {f: canonical_family(f) for f in data["family"].dropna().unique()}
    data["family"] = data["family"].map(families).fillna("")
    data["page"] = data["page"].map(lambda p: None if parse_number(p) is None else int(parse_number(p)))
    data["note"] = ""
    data["based_on"] = [[] for _ in range(len(data))]

    # status: start from the CSV, then let the code be stricter (never more trusting)
    status = data["status"].fillna("unverified").str.lower()
    status = status.where(status.isin(config.CSV_STATUSES), "unverified")
    no_source = status.isin(config.TRUSTED) & (data["page"].isna() | data["quote"].isna())
    status[no_source] = "unverified"
    data.loc[no_source, "note"] = "no page/quote in CSV -> unverified"

    normed = [normalize(v, u) for v, u in zip(data["value"], data["unit"])]
    data["norm_value"] = [n.value for n in normed]
    data["norm_unit"] = [n.unit for n in normed]
    unknown = pd.Series([n.problem == "unknown unit" for n in normed], index=data.index)
    status[unknown] = "unknown unit"
    status[data["value"].isna()] = "missing"
    data["status"] = status

    data = _add_calculated(data)
    data = _flag_conflicts(data)
    return data.reset_index(drop=True)


def _usable(rows: pd.DataFrame) -> pd.DataFrame:
    """Rows that can feed a calculation: have a number and are not in doubt."""
    return rows[rows["norm_value"].notna() & ~rows["status"].isin(["conflict", "missing", "unknown unit"])]


def _add_calculated(data: pd.DataFrame) -> pd.DataFrame:
    # conflicts must be known first, so a calculation never uses a disputed input
    data = _flag_conflicts(data)
    new = []
    for calc in CALCULATIONS:
        for pid, g in data[data["family"] == calc["family"]].groupby("product_id"):
            if (g["attribute"] == calc["attribute"]).any() and \
                    g.loc[g["attribute"] == calc["attribute"], "status"].ne("missing").any():
                continue                                    # the source states it -> don't calculate
            if g[g["attribute"].isin(calc["inputs"])]["status"].eq("conflict").any():
                continue
            usable = _usable(g)
            inputs = [usable[usable["attribute"] == a] for a in calc["inputs"]]
            if any(i.empty for i in inputs):
                continue
            values = [i.sort_values("status", key=lambda s: s.map(config.STATUS_RANK)).iloc[0]["norm_value"]
                      for i in inputs]
            result = round(calc["compute"](*values), 1)
            row = g.iloc[0].to_dict()
            row.update(attribute=calc["attribute"], value=str(result), unit=calc["unit"],
                       norm_value=result, norm_unit=calc["unit"], source_file="calculated", page=None,
                       quote=calc["formula"], status="calculated", csv_line=None,
                       note="calculated by code", based_on=list(calc["inputs"]))
            new.append(row)
    return pd.concat([data, pd.DataFrame(new)], ignore_index=True) if new else data


def _flag_conflicts(data: pd.DataFrame) -> pd.DataFrame:
    """Same product + attribute, 2+ different sources (another file, or another page of the same file),
    values differ by more than the tolerance."""
    numeric = data[data["norm_value"].notna() & (data["source_file"] != "calculated")]
    for (pid, attr), g in numeric.groupby(["product_id", "attribute"]):
        if len(set(zip(g["source_file"], g["page"].fillna(-1)))) < 2:
            continue
        tolerance = load_logic(g["family"].iloc[0]).settings["conflict_tolerance"]
        lo, hi = g["norm_value"].min(), g["norm_value"].max()
        if (hi - lo) / max(abs(hi), 1e-9) > tolerance:
            data.loc[g.index, "status"] = "conflict"
            data.loc[g.index, "note"] = f"{len(g)} sources disagree"
    return data


def products(data: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per product: id, brand, name, family, number of attributes, files it comes from."""
    data = load_repository() if data is None else data
    cols = ["product_id", "brand", "product_name", "family", "attributes", "files"]
    if data.empty:
        return pd.DataFrame(columns=cols)
    return (data.groupby("product_id")
            .agg(brand=("brand", "first"), product_name=("product_name", "first"), family=("family", "first"),
                 attributes=("attribute", "nunique"),
                 files=("csv_file", lambda s: ", ".join(sorted(set(s.dropna())))))
            .reset_index()[cols])


def product_rows(data: pd.DataFrame, product_id: str) -> pd.DataFrame:
    return data[data["product_id"] == product_id]


def best_values(data: pd.DataFrame, product_id: str) -> pd.DataFrame:
    """One row per attribute for a product: the most trustworthy row (manual/ok before unverified ...).
    Index = attribute. Used for ranking only - the fact sheet always shows ALL rows."""
    g = product_rows(data, product_id).copy()
    g["_rank"] = g["status"].map(config.STATUS_RANK).fillna(9)
    return g.sort_values("_rank", kind="stable").groupby("attribute").first()


__all__ = ["load_repository", "repository_report", "validate_csv", "validate_csv_file", "products",
           "read_csv_file", "best_values", "product_rows"]
