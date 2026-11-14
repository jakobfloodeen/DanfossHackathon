"""
QUOTES - is a quote really on the page it cites? (protects against invented values)

    verify_quote("Max Pressure [MPa] 17.2", page_text)   -> True / False

Line breaks and repeated spaces are ignored (PDF text breaks lines anywhere). A small fuzzy margin
(score >= 90) allows for odd characters from PDF extraction, never for a different number:
the numbers in the quote must also appear on the page exactly.

The PDF->CSV converter should use this to set status 'ok' (quote found) or 'unverified' (not found).
"""
import re

from rapidfuzz import fuzz


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def verify_quote(quote: str | None, page_text: str | None, min_score: int = 90) -> bool:
    q, page = _norm(quote), _norm(page_text)
    if not q or not page:
        return False
    if q in page:
        return True
    numbers_ok = all(n in page for n in re.findall(r"\d+(?:[.,]\d+)?", q))
    return numbers_ok and fuzz.partial_ratio(q, page) >= min_score
