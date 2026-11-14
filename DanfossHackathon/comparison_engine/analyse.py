"""
ANALYSE - the one function the website calls:

    result = analyse("A10VO71", "Which Danfoss pump replaces it?", "OEM, truck cranes, needs LS control")

    search -> pick the target -> shortlist -> fact sheet -> AI interpretation -> guardrails -> result

result.status tells the website what to show:
    not_found        nothing matches -> offer the PDF upload
    needs_choice     several products match equally well -> show result.search_hits, let the user pick,
                     then call analyse(<picked product_id>, ...)
    ok               AI answer, checked by code, no problems
    ok_with_problems AI answer, but code found problems -> show result.problems next to it
    no_ai            facts + code differences only (no key / API error / use_ai=False) -> show result.message
"""
from typing import Callable

from .interpret import interpret
from .repository import load_repository
from .results import STATUS_MESSAGES, AnalysisResult
from .search import search

CLEAR_WINNER_GAP = 5     # the top search hit must beat the next one by this many points to be picked


def analyse(query_or_product_id: str, user_question: str = "", customer_context: str = "",
            must_haves: dict | None = None, focus_attributes: list[str] | None = None,
            use_ai: bool = True, ask: Callable | None = None) -> AnalysisResult:
    data = load_repository()
    query = (query_or_product_id or "").strip()
    if query in set(data["product_id"]):
        target_id, hits = query, []
    else:
        found = search(query, data=data)
        hits = found.to_dict("records")
        if found.empty:
            return AnalysisResult(status="not_found", message=STATUS_MESSAGES["not_found"], query=query)
        top = found.iloc[0]["score"]
        second = found.iloc[1]["score"] if len(found) > 1 else -100
        if top - second < CLEAR_WINNER_GAP:
            return AnalysisResult(status="needs_choice", message=STATUS_MESSAGES["needs_choice"],
                                  query=query, search_hits=hits)
        target_id = found.iloc[0]["product_id"]

    result = interpret(target_id, None, user_question, customer_context, focus_attributes=focus_attributes,
                       ask=ask, use_ai=use_ai, must_haves=must_haves, data=data)
    result.query, result.search_hits = query, hits
    return result
