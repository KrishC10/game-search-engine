"""Builds the Elasticsearch Query DSL for a search. Pure functions: no network calls."""
from typing import Dict, List, Optional, Tuple

from api.models import Clues, SearchParams, Sort

# Field weights: a title hit matters most, then tags, then descriptions.
SEARCH_FIELDS = ["name^3", "tags.text^2", "short_description^1.5", "description"]
NAME_PHRASE_BOOST = 5
NAME_EXACT_BOOST = 20

# Popularity tie-breaker. The final score is text_score * (1 + popularity + rating),
# where popularity = 0.1 * log10(1 + review_count) (about 0-0.7)
# and rating = 0.003 * review_score (0-0.3). Text relevance still dominates.
POPULARITY_WEIGHT = 0.1
RATING_WEIGHT = 0.003

GENRE_FACET_SIZE = 20
PRICE_BUCKETS = [
    {"key": "Free", "to": 0.01},
    {"key": "Under $10", "from": 0.01, "to": 10},
    {"key": "$10–30", "from": 10, "to": 30},
    {"key": "Over $30", "from": 30},
]

# Filters on these fields have facets, so they go in post_filter (see build_search_body).
FACETED = ("genres", "price", "years")

RESULT_FIELDS = [
    "app_id", "name", "short_description", "genres", "tags", "price", "is_free",
    "release_date", "release_year", "review_score", "review_count", "header_image",
]

# Tip-of-the-tongue search also looks at how players describe the game.
FIND_FIELDS = ["name^2", "tags.text^2", "short_description^1.5", "description", "review_snippets^1.5"]
YEAR_SLACK = 3  # memories are often a few years off
YEAR_CLUE_BOOST = 2
GENRE_CLUE_BOOST = 1.5

KNN_CANDIDATES_FACTOR = 4
MAX_KNN_CANDIDATES = 10_000
AUTOCOMPLETE_SIZE = 8

SORTS = {
    Sort.relevance: ["_score", {"review_count": {"order": "desc", "missing": "_last"}}],
    Sort.price_asc: [{"price": {"order": "asc", "missing": "_last"}}, "_score"],
    Sort.price_desc: [{"price": {"order": "desc", "missing": "_last"}}, "_score"],
    Sort.rating: [
        {"review_score": {"order": "desc", "missing": "_last"}},
        {"review_count": {"order": "desc", "missing": "_last"}},
    ],
    Sort.newest: [{"release_date": {"order": "desc", "missing": "_last"}}, "_score"],
}


def text_query(q: str) -> dict:
    if not q:
        return {"match_all": {}}
    return {
        "bool": {
            "must": [{
                "multi_match": {
                    "query": q,
                    "fields": SEARCH_FIELDS,
                    "type": "best_fields",
                    "tie_breaker": 0.3,
                    "fuzziness": "AUTO",
                }
            }],
            "should": [
                {"match_phrase": {"name": {"query": q, "boost": NAME_PHRASE_BOOST}}},
                {"term": {"name.keyword": {"value": q, "boost": NAME_EXACT_BOOST}}},
            ],
        }
    }


def with_popularity(query: dict) -> dict:
    return {
        "function_score": {
            "query": query,
            "functions": [
                {"weight": 1},
                {
                    "field_value_factor": {"field": "review_count", "modifier": "log1p", "missing": 0},
                    "weight": POPULARITY_WEIGHT,
                },
                {
                    "field_value_factor": {"field": "review_score", "missing": 0},
                    "weight": RATING_WEIGHT,
                },
            ],
            "score_mode": "sum",
            "boost_mode": "multiply",
        }
    }


def build_filters(p: SearchParams) -> Dict[str, dict]:
    """One filter clause per active filter, keyed by name."""
    filters = {}
    if p.genres:
        filters["genres"] = {"terms": {"genres": p.genres}}  # match any selected genre
    if p.price_min is not None or p.price_max is not None:
        filters["price"] = {"range": {"price": _bounds(p.price_min, p.price_max)}}
    if p.year_from is not None or p.year_to is not None:
        filters["years"] = {"range": {"release_date": {
            **_bounds(
                f"{p.year_from}-01-01" if p.year_from is not None else None,
                f"{p.year_to}-12-31" if p.year_to is not None else None,
            ),
            "format": "yyyy-MM-dd",
        }}}
    if p.min_score is not None:
        filters["min_score"] = {"range": {"review_score": {"gte": p.min_score}}}
    if p.free_only:
        filters["free"] = {"term": {"is_free": True}}
    return filters


def _bounds(low, high) -> dict:
    bounds = {}
    if low is not None:
        bounds["gte"] = low
    if high is not None:
        bounds["lte"] = high
    return bounds


def build_aggs(facet_filters: Dict[str, dict]) -> dict:
    """Facet aggregations. Each facet is filtered by every *other* faceted filter,
    so ticking 'Action' still shows counts for the other genres you could add."""
    facets = {
        "genres": {"terms": {"field": "genres", "size": GENRE_FACET_SIZE}},
        "price": {"range": {"field": "price", "ranges": PRICE_BUCKETS}},
        "years": {"date_histogram": {
            "field": "release_date", "calendar_interval": "year", "format": "yyyy", "min_doc_count": 1,
        }},
    }
    aggs = {}
    for name, agg in facets.items():
        others = [clause for key, clause in facet_filters.items() if key != name]
        aggs[name] = {"filter": {"bool": {"filter": others}}, "aggs": {"buckets": agg}}
    return aggs


def build_search_body(p: SearchParams) -> dict:
    filters = build_filters(p)
    query_filters: List[dict] = [c for key, c in filters.items() if key not in FACETED]
    facet_filters = {key: c for key, c in filters.items() if key in FACETED}

    query = with_popularity(text_query(p.q))
    if query_filters:
        # Filter context: narrows results without changing scores, and is cacheable.
        query = {"bool": {"must": [query], "filter": query_filters}}

    body = {
        "query": query,
        "from": (p.page - 1) * p.size,
        "size": p.size,
        "sort": SORTS[p.sort],
        "_source": RESULT_FIELDS,
        "track_total_hits": True,
        "aggs": build_aggs(facet_filters),
    }
    if facet_filters:
        # post_filter applies after aggregations run, which is what lets build_aggs
        # compute "what if" counts for each facet.
        body["post_filter"] = {"bool": {"filter": list(facet_filters.values())}}
    return body


def all_filters(p: SearchParams) -> List[dict]:
    return list(build_filters(p).values())


def build_knn(field: str, vector: List[float], k: int, filters: Optional[List[dict]] = None) -> dict:
    knn = {
        "field": field,
        "query_vector": vector,
        "k": k,
        "num_candidates": min(k * KNN_CANDIDATES_FACTOR, MAX_KNN_CANDIDATES),
    }
    if filters:
        # Filtering inside the knn search keeps k results that all match,
        # instead of finding k neighbours and then throwing some away.
        knn["filter"] = {"bool": {"filter": filters}}
    return knn


def build_semantic_body(p: SearchParams, vector: List[float], k: int, with_aggs: bool = True) -> dict:
    body = {
        "knn": build_knn("description_vector", vector, k, all_filters(p)),
        "size": k,
        "_source": RESULT_FIELDS,
    }
    if with_aggs:
        body["aggs"] = build_aggs({})  # counts over the k nearest games
    return body


def build_autocomplete_body(q: str) -> dict:
    return {
        "query": with_popularity({
            "multi_match": {
                "query": q,
                "type": "bool_prefix",
                "fields": ["name.suggest", "name.suggest._2gram", "name.suggest._3gram"],
            }
        }),
        "size": AUTOCOMPLETE_SIZE,
        "_source": ["app_id", "name"],
    }


def build_similar_body(index: str, app_id: str, size: int) -> dict:
    return {
        "query": with_popularity({
            "bool": {
                "must": [{
                    "more_like_this": {
                        "fields": ["description", "tags.text"],
                        "like": [{"_index": index, "_id": app_id}],
                        "min_term_freq": 1,
                        "min_doc_freq": 3,
                        "max_query_terms": 30,
                    }
                }],
                "must_not": [{"ids": {"values": [app_id]}}],
            }
        }),
        "size": size,
        "_source": RESULT_FIELDS,
    }


def clue_year_range(clues: Clues) -> Optional[Tuple[int, int]]:
    """Widen the remembered years by YEAR_SLACK each way; one year alone means 'around then'."""
    low = clues.year_from if clues.year_from is not None else clues.year_to
    high = clues.year_to if clues.year_to is not None else clues.year_from
    if low is None or high is None:
        return None
    return low - YEAR_SLACK, high + YEAR_SLACK


def build_find_keyword_body(clues: Clues, size: int) -> dict:
    """Keyword half of tip-of-the-tongue search. Year and genre clues are boosts, not filters."""
    text = " ".join(clues.keywords) or clues.description
    should = []
    if clues.keywords:
        should.append({"multi_match": {"query": clues.description, "fields": FIND_FIELDS, "boost": 0.5}})
    years = clue_year_range(clues)
    if years:
        should.append({"range": {"release_year": {"gte": years[0], "lte": years[1], "boost": YEAR_CLUE_BOOST}}})
    if clues.genres:
        should.append({"terms": {"genres": clues.genres, "boost": GENRE_CLUE_BOOST}})
    query = {
        "bool": {
            "must": [{"multi_match": {"query": text, "fields": FIND_FIELDS, "type": "best_fields", "tie_breaker": 0.3}}],
            "should": should,
        }
    }
    return {"query": with_popularity(query), "size": size, "_source": RESULT_FIELDS}


def build_find_knn_body(field: str, vector: List[float], k: int) -> dict:
    return {"knn": build_knn(field, vector, k), "size": k, "_source": RESULT_FIELDS}
