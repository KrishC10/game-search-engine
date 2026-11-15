import pytest

from api.find import CLUE_BONUS, apply_clue_bonus
from api.models import Clues, Sort
from api.search import RRF_K, fuse, rrf, sort_hits


def hit(doc_id, **source):
    return {"_id": doc_id, "_source": {"app_id": doc_id, "name": doc_id, **source}, "_score": 1.0}


def test_rrf_rewards_games_found_by_both_lists():
    fused = dict(rrf([["a", "b", "c"], ["c", "d"]]))
    assert fused["c"] == pytest.approx(1 / (RRF_K + 3) + 1 / (RRF_K + 1))
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert max(fused, key=fused.get) == "c"


def test_fuse_returns_hits_ranked_by_fused_score():
    fused = fuse([[hit("a"), hit("b")], [hit("b"), hit("c")]])
    assert [h["_id"] for h in fused] == ["b", "a", "c"]
    assert fused[0]["_source"]["name"] == "b"


def test_sort_hits_puts_missing_values_last_and_keeps_relevance_ties():
    hits = [hit("a", price=10), hit("b"), hit("c", price=5), hit("d", price=10)]
    assert [h["_id"] for h in sort_hits(hits, Sort.price_asc)] == ["c", "a", "d", "b"]
    assert [h["_id"] for h in sort_hits(hits, Sort.price_desc)] == ["a", "d", "c", "b"]
    assert sort_hits(hits, Sort.relevance) == hits


def test_clue_bonus_nudges_matching_year_and_genre():
    hits = [
        {**hit("old", release_year=2001, genres=["Action"]), "_score": 0.02},
        {**hit("match", release_year=2013, genres=["Indie"]), "_score": 0.02},
    ]
    clues = Clues(description="x", year_from=2015, year_to=2015, genres=["Indie"])
    ranked = apply_clue_bonus(hits, clues)
    assert ranked[0]["_id"] == "match"
    assert ranked[0]["_score"] == pytest.approx(0.02 + 2 * CLUE_BONUS)
    assert ranked[1]["_score"] == pytest.approx(0.02)
