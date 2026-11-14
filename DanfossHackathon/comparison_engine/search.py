"""
SEARCH - find a product from free text, tolerant to spelling and spacing.

    search("A10VO71")  ==  search("a10vo 71")  ==  search("Rexroth A10VO-71")
    search("danfoss 75")   -> only products where BOTH words match (pump 45 is NOT returned)
    search("xyz")          -> empty table -> the website offers the PDF upload

How it works (simple on purpose):
  1. Every product gets a word pool: brand, product name, product_id, family and model codes.
     Words are split at spaces/symbols AND between letters and digits ("pump71" -> "pump", "71").
  2. If the whole query without spaces/symbols appears in the product text -> score 100.
  3. Otherwise EVERY query word must match a word in the pool:
       numbers must match exactly ("75" never matches "750" or "45"),
       letter words may have small typos (fuzzy ratio >= 80, e.g. "compettorx" ~ "competitorx").
     Score = average word score. One unmatched word -> the product is not a hit.
"""
import re

import pandas as pd
from rapidfuzz import fuzz

from .repository import load_repository, products

CODE_ATTRIBUTES = ("model_code", "type_code", "order_code", "ordering_code", "part_number", "article")
RESULT_COLUMNS = ["product_id", "brand", "product_name", "family", "score"]


def squash(text) -> str:
    """'Rexroth A10VO-71' -> 'rexrotha10vo71'"""
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def words(text) -> list[str]:
    """'A10VO-71 pump71' -> ['a', '10', 'vo', '71', 'pump', '71']"""
    return re.findall(r"[a-z]+|\d+", str(text).lower())


def _word_score(query_word: str, pool: set[str]) -> float:
    if query_word in pool:
        return 100.0
    if query_word.isdigit():
        return 0.0                                    # numbers must match exactly
    if len(query_word) <= 2:
        return 0.0                                    # too short to be fuzzy about
    return max((fuzz.ratio(query_word, w) for w in pool if not w.isdigit()), default=0.0)


def _product_texts(data: pd.DataFrame) -> dict[str, list[str]]:
    """product_id -> list of texts to search in (names + model codes)."""
    texts = {}
    is_code = data["attribute"].str.contains("|".join(CODE_ATTRIBUTES), na=False)
    codes = data[is_code & data["value"].notna()].groupby("product_id")["value"].apply(list)
    for _, p in products(data).iterrows():
        pid = p["product_id"]
        texts[pid] = [p["brand"], p["product_name"], pid, p["family"]] + codes.get(pid, [])
    return texts


def search(query: str, limit: int = 10, min_word_score: float = 80, data: pd.DataFrame | None = None,
           family: str | None = None, brand: str | None = None) -> pd.DataFrame:
    """Fuzzy product search. Returns product_id, brand, product_name, family, score (0-100), best first."""
    data = load_repository() if data is None else data
    if not query or not str(query).strip() or data.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    q_squashed, q_words = squash(query), words(query)
    prods = products(data).set_index("product_id")
    hits = []
    for pid, texts in _product_texts(data).items():
        if family and prods.loc[pid, "family"] != family.lower():
            continue
        if brand and str(prods.loc[pid, "brand"]).lower() != brand.lower():
            continue
        if q_squashed and any(q_squashed in squash(t) for t in texts if t):
            score = 100.0
        else:
            pool = {w for t in texts if t for w in words(t)}
            scores = [_word_score(w, pool) for w in q_words]
            if not scores or min(scores) < min_word_score:
                continue
            score = sum(scores) / len(scores)
        p = prods.loc[pid]
        hits.append({"product_id": pid, "brand": p["brand"], "product_name": p["product_name"],
                     "family": p["family"], "score": round(score)})
    if not hits:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    return (pd.DataFrame(hits, columns=RESULT_COLUMNS)
            .sort_values(["score", "product_id"], ascending=[False, True]).head(limit).reset_index(drop=True))
