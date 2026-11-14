"""
Settings for the comparison engine. Change things HERE, not inside the other modules.

Environment variables (optional, e.g. in .env):
    REPO_DIR     folder with the product CSVs (default: ./data/repository - the PDF->CSV tool writes here)
    RULES_DIR    folder with the LOGIC files (default: ./rules)
    LLM_MODEL    Claude model name (default: claude-sonnet-5-5)
    LLM_TIMEOUT  seconds before we give up on the AI (default: 90)
    CACHE_DIR    where interpret() results are stored (default: ./.cache/interpret)
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def repo_dir() -> Path:
    """Folder the website saves the CSVs into. Read every time, so tests can change the env var."""
    return Path(os.getenv("REPO_DIR", PROJECT_ROOT / "data" / "repository"))


def rules_dir() -> Path:
    return Path(os.getenv("RULES_DIR", PROJECT_ROOT / "rules"))


def cache_dir() -> Path:
    return Path(os.getenv("CACHE_DIR", PROJECT_ROOT / ".cache" / "interpret"))


def llm_model() -> str:
    return os.getenv("LLM_MODEL", "claude-sonnet-5-5")


def llm_timeout() -> float:
    return float(os.getenv("LLM_TIMEOUT", "90"))


# ---------------------------------------------------------------- the agreed CSV format
# Our name -> the column name in the CSV files. If the team's headers change, edit ONLY the right side.
COLUMNS = {
    "product_id": "product_id",      # optional: built from "brand|product_name" if empty
    "brand": "brand",
    "product_name": "product_name",
    "family": "family",
    "attribute": "attribute",
    "value": "value",
    "unit": "unit",
    "source_file": "source_file",
    "page": "page",
    "quote": "quote",
    "status": "status",              # optional: defaults to "unverified"
}
REQUIRED = ["brand", "product_name", "family", "attribute", "value", "unit", "source_file"]

# ---------------------------------------------------------------- status flags
# ok          the quote was found on the cited page
# manual      a human corrected / confirmed the value
# unverified  the value has a source, but the quote was not confirmed
# missing     the source does not state this value (never guessed)
# conflict    2+ sources give different values (all values stay visible)
# calculated  computed by our code from other values (formula is shown)
# unknown unit  the unit is not in our unit table, so the number can't be compared
STATUSES = ["ok", "manual", "unverified", "missing", "conflict", "calculated", "unknown unit"]
CSV_STATUSES = ["ok", "manual", "unverified", "missing"]   # what a CSV file may contain
TRUSTED = {"ok", "manual"}
# lower = more trustworthy; used to pick the "best" value when a product has several rows
STATUS_RANK = {"manual": 0, "ok": 0, "calculated": 1, "conflict": 2, "unverified": 3,
               "unknown unit": 4, "missing": 5}

DANFOSS_BRAND = "Danfoss"
