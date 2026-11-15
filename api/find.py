"""Tip-of-the-tongue search: find a game from a vague, half-remembered description.

1. Understand: the LLM turns the memory into clues and a clean one-sentence query
   (api/understand.py). Without AI, the raw text is the query.
2. Search: three ranked lists in one multi-search — keywords across names, tags,
   descriptions and review snippets; and nearest neighbours of the clean query on
   the description vectors and on the review vectors.
3. Fuse the lists with RRF, then nudge games that match the year and genre clues.
   Clues are boosts, never filters, because memories are often wrong.

The LLM never picks the answer; Elasticsearch does the finding.
"""
import time
from typing import List

from elasticsearch import Elasticsearch

from api.embeddings import SemanticUnavailable, encode_query
from api.es import INDEX
from api.models import Clues, FindResponse
from api.queries import build_find_keyword_body, build_find_knn_body, clue_year_range
from api.search import RRF_K, fuse, multi_search, to_result
from api.understand import understand

FIND_WINDOW = 50
FIND_RESULTS = 10
# A clue match is worth half of a first place in one list.
CLUE_BONUS = 0.5 / (RRF_K + 1)


def raw_clues(memory: str) -> Clues:
    return Clues(description=memory.strip())


def find(es: Elasticsearch, memory: str, use_ai: bool = True, index: str = INDEX) -> FindResponse:
    started = time.perf_counter()
    clues = understand(memory) if use_ai else None
    used_ai = clues is not None
    clues = clues or raw_clues(memory)

    bodies = [build_find_keyword_body(clues, FIND_WINDOW)]
    semantic = True
    try:
        vector = encode_query(clues.description)
        bodies += [build_find_knn_body(field, vector, FIND_WINDOW) for field in ("description_vector", "review_vector")]
    except SemanticUnavailable:
        semantic = False

    responses = multi_search(es, index, bodies)
    hits = apply_clue_bonus(fuse([r["hits"]["hits"] for r in responses]), clues)
    return FindResponse(
        clues=clues,
        query=clues.description,
        used_ai=used_ai,
        semantic=semantic,
        took_ms=round((time.perf_counter() - started) * 1000),
        results=[to_result(hit) for hit in hits[:FIND_RESULTS]],
    )


def apply_clue_bonus(hits: List[dict], clues: Clues) -> List[dict]:
    """Soft boosts for the semantic lists too: the keyword query already boosts these in Elasticsearch."""
    years = clue_year_range(clues)
    genres = set(clues.genres)
    for hit in hits:
        source = hit["_source"]
        year = source.get("release_year")
        if years and year is not None and years[0] <= year <= years[1]:
            hit["_score"] += CLUE_BONUS
        if genres & set(source.get("genres", [])):
            hit["_score"] += CLUE_BONUS
    return sorted(hits, key=lambda h: h["_score"], reverse=True)
