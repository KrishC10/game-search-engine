import pytest
from pydantic import ValidationError

from api.models import Clues, SearchParams, Sort
from api.queries import (
    SORTS, build_autocomplete_body, build_find_keyword_body, build_knn, build_search_body,
    build_semantic_body, build_similar_body, clue_year_range,
)


def body(**params):
    return build_search_body(SearchParams(**params))


def inner_query(b):
    """The text query inside function_score (and inside the filter bool, if any)."""
    q = b["query"]
    if "bool" in q:
        q = q["bool"]["must"][0]
    return q["function_score"]["query"]


def test_empty_query_matches_everything_ranked_by_popularity():
    b = body()
    assert inner_query(b) == {"match_all": {}}
    functions = b["query"]["function_score"]["functions"]
    assert {"weight": 1} in functions
    assert any(f.get("field_value_factor", {}).get("field") == "review_count" for f in functions)
    assert "post_filter" not in b


def test_text_query_is_fuzzy_multi_match_with_name_boosts():
    q = inner_query(body(q="  stardew valey "))["bool"]
    multi = q["must"][0]["multi_match"]
    assert multi["query"] == "stardew valey"
    assert multi["fuzziness"] == "AUTO"
    assert multi["fields"][0] == "name^3"
    assert q["should"][0]["match_phrase"]["name"]["query"] == "stardew valey"
    assert q["should"][1]["term"]["name.keyword"]["value"] == "stardew valey"


def test_genres_go_in_post_filter_and_are_excluded_from_their_own_facet():
    b = body(genres=["Action", "RPG"])
    genre_filter = {"terms": {"genres": ["Action", "RPG"]}}
    assert b["post_filter"]["bool"]["filter"] == [genre_filter]
    assert b["aggs"]["genres"]["filter"]["bool"]["filter"] == []
    assert b["aggs"]["price"]["filter"]["bool"]["filter"] == [genre_filter]
    assert b["aggs"]["years"]["filter"]["bool"]["filter"] == [genre_filter]


def test_price_and_year_ranges():
    b = body(price_min=10, price_max=29.99, year_from=2015, year_to=2020)
    price, years = b["post_filter"]["bool"]["filter"]
    assert price == {"range": {"price": {"gte": 10, "lte": 29.99}}}
    assert years == {"range": {"release_date": {"gte": "2015-01-01", "lte": "2020-12-31", "format": "yyyy-MM-dd"}}}
    assert b["aggs"]["price"]["filter"]["bool"]["filter"] == [years]


def test_open_ended_price_range():
    b = body(price_max=0)
    assert b["post_filter"]["bool"]["filter"] == [{"range": {"price": {"lte": 0}}}]


def test_score_and_free_filters_go_in_query_filter_context():
    b = body(q="racing", min_score=80, free_only=True)
    filters = b["query"]["bool"]["filter"]
    assert {"range": {"review_score": {"gte": 80}}} in filters
    assert {"term": {"is_free": True}} in filters
    assert "post_filter" not in b
    # Non-faceted filters narrow the facet counts too, because they are in the query.
    assert b["aggs"]["genres"]["filter"]["bool"]["filter"] == []


def test_all_facets_requested():
    aggs = body()["aggs"]
    assert aggs["genres"]["aggs"]["buckets"]["terms"]["size"] == 20
    assert [r["key"] for r in aggs["price"]["aggs"]["buckets"]["range"]["ranges"]] == [
        "Free", "Under $10", "$10–30", "Over $30",
    ]
    assert aggs["years"]["aggs"]["buckets"]["date_histogram"]["calendar_interval"] == "year"


@pytest.mark.parametrize("sort", list(Sort))
def test_sorts(sort):
    assert body(sort=sort)["sort"] == SORTS[sort]


def test_pagination():
    b = body(page=3, size=20)
    assert b["from"] == 40
    assert b["size"] == 20


@pytest.mark.parametrize("params", [
    {"price_min": 30, "price_max": 10},
    {"year_from": 2020, "year_to": 2010},
    {"size": 51},
    {"page": 0},
    {"page": 501, "size": 20},
    {"min_score": 101},
    {"sort": "popularity"},
])
def test_invalid_params_rejected(params):
    with pytest.raises(ValidationError):
        SearchParams(**params)


def test_semantic_body_filters_inside_knn():
    p = SearchParams(q="cozy farming", genres=["Simulation"], free_only=True)
    b = build_semantic_body(p, [0.1] * 384, k=100)
    knn = b["knn"]
    assert knn["field"] == "description_vector"
    assert knn["k"] == 100 and knn["num_candidates"] == 400
    assert {"terms": {"genres": ["Simulation"]}} in knn["filter"]["bool"]["filter"]
    assert {"term": {"is_free": True}} in knn["filter"]["bool"]["filter"]
    assert "aggs" in b
    assert "aggs" not in build_semantic_body(p, [0.1] * 384, k=100, with_aggs=False)


def test_knn_without_filters_has_no_filter_clause():
    assert "filter" not in build_knn("review_vector", [0.0] * 384, 50)


def test_autocomplete_uses_bool_prefix_on_suggest_fields():
    b = build_autocomplete_body("star")
    multi = b["query"]["function_score"]["query"]["multi_match"]
    assert multi["type"] == "bool_prefix"
    assert multi["fields"][0] == "name.suggest"
    assert b["size"] == 8


def test_similar_excludes_the_game_itself():
    b = build_similar_body("games", "413150", 6)
    q = b["query"]["function_score"]["query"]["bool"]
    assert q["must"][0]["more_like_this"]["like"] == [{"_index": "games", "_id": "413150"}]
    assert q["must_not"] == [{"ids": {"values": ["413150"]}}]
    assert b["size"] == 6


@pytest.mark.parametrize("year_from, year_to, expected", [
    (2015, 2015, (2012, 2018)),
    (2015, None, (2012, 2018)),
    (None, 2010, (2007, 2013)),
    (2007, 2009, (2004, 2012)),
    (None, None, None),
])
def test_clue_year_range_widens_by_three(year_from, year_to, expected):
    assert clue_year_range(Clues(description="x", year_from=year_from, year_to=year_to)) == expected


def test_find_keyword_body_uses_clues_as_boosts_not_filters():
    clues = Clues(description="A fox in a ruined city.", keywords=["fox", "ruined city"],
                  year_from=2015, year_to=2015, genres=["Adventure"])
    q = build_find_keyword_body(clues, 50)["query"]["function_score"]["query"]["bool"]
    assert q["must"][0]["multi_match"]["query"] == "fox ruined city"
    assert "review_snippets^1.5" in q["must"][0]["multi_match"]["fields"]
    assert {"range": {"release_year": {"gte": 2012, "lte": 2018, "boost": 2}}} in q["should"]
    assert {"terms": {"genres": ["Adventure"], "boost": 1.5}} in q["should"]
    assert "filter" not in q


def test_find_keyword_body_without_keywords_searches_the_description():
    q = build_find_keyword_body(Clues(description="some memory"), 50)["query"]["function_score"]["query"]["bool"]
    assert q["must"][0]["multi_match"]["query"] == "some memory"
    assert q["should"] == []
