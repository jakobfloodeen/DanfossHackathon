"""
LOGIC files (written by Otto) = the comparison "skill".

    rules/_general.md   general logic for ANY product (always used)
    rules/<family>.md   optional extra logic for one family (e.g. pump.md)

Each file = YAML settings between two '---' lines (read by CODE: weights, better, tolerances, filters)
          + text below it (read by the AI: goes into the system prompt).
The family file overrides general settings with the same name; the texts are joined.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

DEFAULTS = {
    "danfoss_brand": config.DANFOSS_BRAND,
    "family_aliases": {},
    "attribute_aliases": {},     # datasheet name -> standard name (rules/<family>.md)
    "weights": {},
    "better": {},
    "hard_filters": {"within": {}, "at_least": {}},
    "size_attribute": None,
    "size_window": 0.5,
    "shortlist_size": 8,
    "similar_within": 0.03,
    "conflict_tolerance": 0.02,
}


@dataclass
class Logic:
    family: str
    settings: dict
    text: str                                       # goes into the AI system prompt
    files: list[str] = field(default_factory=list)  # which files were used (shown in the result)

    def size_attribute(self) -> str | None:
        """Attribute that describes product size: explicit setting > first 'within' filter > top weight."""
        s = self.settings
        if s.get("size_attribute"):
            return s["size_attribute"]
        within = s["hard_filters"].get("within") or {}
        if within:
            return next(iter(within))
        weights = s.get("weights") or {}
        return max(weights, key=weights.get) if weights else None


def read_logic_file(path: Path) -> tuple[dict, str]:
    """-> (settings dict, text). A file without YAML front matter is all text."""
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
    if not m:
        return {}, raw.strip()
    settings = yaml.safe_load(m.group(1)) or {}
    if not isinstance(settings, dict):
        raise ValueError(f"{path.name}: the YAML part must be 'name: value' lines")
    return settings, m.group(2).strip()


def _merge(base: dict, extra: dict) -> dict:
    """Like {**base, **extra}, but dicts inside (weights, better, hard_filters...) are merged too."""
    out = dict(base)
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


_CACHE: dict = {}


def load_logic(family: str = "", rules_dir: Path | None = None) -> Logic:
    """General logic + the family's logic (if a file exists). Re-reads automatically when a file changes."""
    folder = Path(rules_dir) if rules_dir else config.rules_dir()
    family = canonical_family(family, folder)
    paths = [folder / "_general.md", folder / f"{family}.md"] if family else [folder / "_general.md"]
    paths = [p for p in paths if p.exists()]
    key = (str(folder), family, tuple((str(p), p.stat().st_mtime_ns) for p in paths))
    if key in _CACHE:
        return _CACHE[key]

    settings, texts = dict(DEFAULTS), []
    for p in paths:
        s, text = read_logic_file(p)
        settings = _merge(settings, s)
        if text:
            texts.append(text)
    settings["hard_filters"] = _merge(DEFAULTS["hard_filters"], settings.get("hard_filters") or {})
    for k in ("weights", "better", "family_aliases", "attribute_aliases"):
        settings[k] = settings.get(k) or {}
    logic = Logic(family=family, settings=settings, text="\n\n".join(texts), files=[p.name for p in paths])
    _CACHE[key] = logic
    return logic


def canonical_family(name, rules_dir: Path | None = None) -> str:
    """'Axial Piston Pump ' -> 'pump' using family_aliases from _general.md."""
    if name is None or (isinstance(name, float) and name != name):
        return ""
    clean = " ".join(str(name).lower().split())
    general = (Path(rules_dir) if rules_dir else config.rules_dir()) / "_general.md"
    aliases = {}
    if general.exists():
        try:
            aliases = read_logic_file(general)[0].get("family_aliases") or {}
        except (ValueError, yaml.YAMLError):
            aliases = {}
    aliases = {" ".join(str(k).lower().split()): str(v).lower() for k, v in aliases.items()}
    return aliases.get(clean, clean)
