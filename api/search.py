"""Runs searches against Elasticsearch and shapes the responses."""
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from elasticsearch import Elasticsearch

from api.embeddings import SemanticUnavailable, encode_query
from api.es import INDEX
from api.models import (
    Bucket, Facets, GameResult, Mode, PriceBucket, SearchParams, SearchResponse, Sort, Suggestion,
)
from api.queries import (
    build_autocomplete_body, build_search_body, build_semantic_body, build_similar_body,
)

RESULT_TAGS = 5
RRF_K = 60  # the standard Reciprocal Rank Fusion constant
FUSION_WINDOW = 100  # how deep each list goes before fusing
MAX_FUSION_WINDOW = 1000

# Sorting semantic and hybrid results happens in Python, on the fused list.
SORT_FIELDS = {
    Sort.price_asc: ("price", False),
    Sort.price_desc: ("price", True),
    Sort.rating: ("review_score", True),
    Sort.newest: ("release_date", True),
}


class SearchFailed(RuntimeError):
    """One of the searches in a multi-search returned an error."""


def search(es: Elasticsearch, p: SearchParams, index: str = INDEX) -> SearchResponse:
    if p.mode is Mode.keyword or not p.q:
        return keyword_search(es, p, index)
    try:
        vector = encode_query(p.q)
    except SemanticUnavailable:
        if p.mode is Mode.semantic:
            raise
        return keyword_search(es, p, index)  # hybrid degrades to keyword
    if p.mode is Mode.semantic:
        return semantic_search(es, p, vector, index)
    return hybrid_search(es, p, vector, index)


def keyword_search(es: Elasticsearch, p: SearchParams, index: str = INDEX) -> SearchResponse:
    resp = es.search(index=index, body=build_search_body(p))
    return SearchResponse(
        total=resp["hits"]["total"]["value"],
        took_ms=resp["took"],
        page=p.page,
        size=p.size,
        mode=Mode.keyword,
        results=[to_result(hit) for hit in resp["hits"]["hits"]],
        facets=to_facets(resp["aggregations"]),
    )


def semantic_search(es: Elasticsearch, p: SearchParams, vector: List[float], index: str = INDEX) -> SearchResponse:
    k = fusion_window(p)
    resp = es.search(index=index, body=build_semantic_body(p, vector, k))
    hits = sort_hits(resp["hits"]["hits"], p.sort)
    return paged_response(hits, p, Mode.semantic, resp["took"], to_facets(resp["aggregations"]))


def hybrid_search(es: Elasticsearch, p: SearchParams, vector: List[float], index: str = INDEX) -> SearchResponse:
    k = fusion_window(p)
    # model_copy skips validation, so the keyword list can be deeper than the API's page size limit.
    keyword_params = p.model_copy(update={"sort": Sort.relevance, "page": 1, "size": k})
    keyword_resp, semantic_resp = multi_search(es, index, [
        build_search_body(keyword_params),
        build_semantic_body(p, vector, k, with_aggs=False),
    ])
    fused = fuse([keyword_resp["hits"]["hits"], semantic_resp["hits"]["hits"]])
    hits = sort_hits(fused, p.sort)
    took = keyword_resp["took"] + semantic_resp["took"]
    # Facets come from the keyword side, which has the disjunctive (post_filter) counts.
    return paged_response(hits, p, Mode.hybrid, took, to_facets(keyword_resp["aggregations"]))


def multi_search(es: Elasticsearch, index: str, bodies: Sequence[dict]) -> List[dict]:
    """Send several searches in one round trip."""
    searches = []
    for body in bodies:
        searches += [{"index": index}, body]
    responses = es.msearch(searches=searches)["responses"]
    for r in responses:
        if "error" in r:
            raise SearchFailed(str(r["error"]))
    return responses


def rrf(ranked_ids: Sequence[Sequence[str]], k: int = RRF_K) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion: score = sum of 1 / (k + rank) across every list a game appears in."""
    scores: Dict[str, float] = defaultdict(float)
    for ids in ranked_ids:
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def fuse(hit_lists: Sequence[List[dict]]) -> List[dict]:
    """RRF over Elasticsearch hit lists; returns hits whose _score is the fused score."""
    sources = {}
    for hits in hit_lists:
        for hit in hits:
            sources.setdefault(hit["_id"], hit["_source"])
    fused = rrf([[hit["_id"] for hit in hits] for hits in hit_lists])
    return [{"_id": doc_id, "_source": sources[doc_id], "_score": score} for doc_id, score in fused]


def sort_hits(hits: List[dict], sort: Sort) -> List[dict]:
    if sort is Sort.relevance:
        return hits
    field, descending = SORT_FIELDS[sort]
    present = [h for h in hits if h["_source"].get(field) is not None]
    missing = [h for h in hits if h["_source"].get(field) is None]
    # sorted() is stable, so ties keep their relevance order.
    return sorted(present, key=lambda h: h["_source"][field], reverse=descending) + missing


def fusion_window(p: SearchParams) -> int:
    return min(max(FUSION_WINDOW, p.page * p.size), MAX_FUSION_WINDOW)


def paged_response(hits: List[dict], p: SearchParams, mode: Mode, took: int, facets: Facets) -> SearchResponse:
    start = (p.page - 1) * p.size
    return SearchResponse(
        total=len(hits),
        took_ms=took,
        page=p.page,
        size=p.size,
        mode=mode,
        results=[to_result(hit) for hit in hits[start:start + p.size]],
        facets=facets,
    )


def autocomplete(es: Elasticsearch, q: str, index: str = INDEX) -> List[Suggestion]:
    resp = es.search(index=index, body=build_autocomplete_body(q))
    return [Suggestion(**hit["_source"]) for hit in resp["hits"]["hits"]]


def similar(es: Elasticsearch, app_id: str, size: int, index: str = INDEX) -> List[GameResult]:
    resp = es.search(index=index, body=build_similar_body(index, app_id, size))
    return [to_result(hit) for hit in resp["hits"]["hits"]]


def to_result(hit: dict) -> GameResult:
    source = dict(hit["_source"])
    source["tags"] = source.get("tags", [])[:RESULT_TAGS]
    return GameResult(**source, score=hit.get("_score"))


def to_facets(aggs: dict) -> Facets:
    return Facets(
        genres=[Bucket(key=b["key"], count=b["doc_count"]) for b in aggs["genres"]["buckets"]["buckets"]],
        price=[
            PriceBucket(
                key=b["key"],
                count=b["doc_count"],
                min=b.get("from") or None,
                # Range aggs exclude "to"; filters include price_max, so step back one cent.
                max=round(b["to"] - 0.01, 2) if "to" in b else None,
            )
            for b in aggs["price"]["buckets"]["buckets"]
        ],
        years=[Bucket(key=b["key_as_string"], count=b["doc_count"]) for b in aggs["years"]["buckets"]["buckets"]],
    )
