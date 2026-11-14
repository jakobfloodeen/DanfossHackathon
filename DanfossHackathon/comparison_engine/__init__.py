"""
comparison_engine - competitor product comparison for Danfoss salespeople (framework-independent).

The website only needs these:

    from comparison_engine import analyse, search, validate_csv, repository_report

    result = analyse("competitorx pump 71", "Best Danfoss replacement?", "OEM, truck-mounted cranes")
    result.status            # ok / ok_with_problems / no_ai / not_found / needs_choice
    result.to_dict()         # everything as plain data (facts, comparison_rows, onepager, problems ...)

Modules (each one small, read them top to bottom):
    config      settings + the agreed CSV column mapping
    units       numbers and units (code, never the AI)
    repository  load all CSVs, validate_csv, conflicts, calculated values
    logic       read Otto's LOGIC files (rules/_general.md + rules/<family>.md)
    search      fuzzy product search
    shortlist   5-10 plausible Danfoss candidates
    facts       numbered facts F1.. + code-computed differences
    llm         the only place that calls the Claude API
    guardrails  code checks the AI's JSON answer
    interpret   the one AI step (+ retry, cache, fallback)
    results     the result object + comparison table + one-pager data
    analyse     search -> shortlist -> facts -> interpret, in one call
"""
from .analyse import analyse
from .facts import Diff, Fact, build_facts, code_differences
from .guardrails import OUTPUT_SCHEMA, check_interpretation
from .interpret import interpret
from .llm import LLMUnavailable, ask_claude, has_api_key
from .logic import Logic, load_logic
from .repository import (best_values, load_repository, products, repository_report, validate_csv,
                         validate_csv_file)
from .results import AnalysisResult
from .search import search
from .shortlist import shortlist
from .units import normalize, parse_number

__all__ = ["analyse", "interpret", "search", "shortlist", "build_facts", "code_differences",
           "check_interpretation", "load_repository", "repository_report", "validate_csv",
           "validate_csv_file", "products", "best_values", "load_logic", "normalize", "parse_number",
           "ask_claude", "has_api_key", "LLMUnavailable", "AnalysisResult", "Fact", "Diff", "Logic",
           "OUTPUT_SCHEMA"]
