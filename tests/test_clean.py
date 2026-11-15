from collections import Counter

import pytest

from ingest.clean import (
    clean_row,
    clean_rows,
    fix_header,
    parse_date,
    parse_price,
    review_score,
    split_list,
    strip_html,
    summarize,
)


def raw(**overrides):
    row = {
        "AppID": "413150",
        "Name": "Stardew Valley",
        "About the game": "<p>You've inherited your grandfather's old <b>farm</b> plot.</p>",
        "Genres": "Indie,RPG,Simulation",
        "Tags": "Farming Sim,Pixel Graphics,Cozy",
        "Developers": "ConcernedApe",
        "Price": "14.99",
        "Release date": "Feb 26, 2016",
        "Positive": "500000",
        "Negative": "10000",
        "Header image": "https://example.com/header.jpg",
    }
    row.update(overrides)
    return row


def test_clean_row_builds_document():
    doc, reason = clean_row(raw())
    assert reason is None
    assert doc["app_id"] == "413150"
    assert doc["description"] == "You've inherited your grandfather's old farm plot."
    assert doc["short_description"] == doc["description"]
    assert doc["genres"] == ["Indie", "RPG", "Simulation"]
    assert doc["developer"] == ["ConcernedApe"]
    assert doc["price"] == 14.99
    assert doc["is_free"] is False
    assert doc["release_date"] == "2016-02-26"
    assert doc["release_year"] == 2016
    assert doc["review_score"] == 98
    assert doc["review_count"] == 510000


@pytest.mark.parametrize("field, reason", [
    ("Name", "missing name"),
    ("About the game", "missing description"),
    ("AppID", "missing app_id"),
])
def test_rows_missing_required_fields_are_skipped(field, reason):
    assert clean_row(raw(**{field: "  "})) == (None, reason)


def test_html_only_description_is_skipped():
    assert clean_row(raw(**{"About the game": "<br><p></p>"})) == (None, "missing description")


def test_free_game():
    doc, _ = clean_row(raw(Price="0.0"))
    assert doc["price"] == 0
    assert doc["is_free"] is True


def test_bad_price_leaves_price_fields_empty():
    doc, _ = clean_row(raw(Price="call us"))
    assert "price" not in doc
    assert "is_free" not in doc


def test_short_description_column_is_used_when_present():
    doc, _ = clean_row(raw(**{"Short description": "A farming sim."}))
    assert doc["short_description"] == "A farming sim."


def test_clean_rows_drops_duplicates_and_counts_skips():
    stats = Counter()
    rows = [raw(), raw(), raw(Name=""), raw(AppID="1", **{"Release date": "someday"})]
    docs = list(clean_rows(rows, stats))
    assert [d["app_id"] for d in docs] == ["413150", "1"]
    assert stats["skipped: duplicate app_id"] == 1
    assert stats["skipped: missing name"] == 1
    assert stats["field left empty: release_date"] == 1
    assert stats["cleaned"] == 2


def test_strip_html():
    assert strip_html("<h2>Hi&nbsp;there</h2>\n<ul><li>one</li></ul>") == "Hi there one"


def test_split_list():
    assert split_list("Action, Indie,,Action") == ["Action", "Indie"]
    assert split_list("") == []


@pytest.mark.parametrize("value, expected", [
    ("19.99", 19.99),
    ("$4.99", 4.99),
    ("0", 0.0),
    ("Free", 0.0),
    ("Free to Play", 0.0),
    ("", None),
    ("abc", None),
    ("-5", None),
])
def test_parse_price(value, expected):
    assert parse_price(value) == expected


@pytest.mark.parametrize("value, expected", [
    ("Oct 21, 2008", "2008-10-21"),
    ("October 21, 2008", "2008-10-21"),
    ("21 Oct, 2008", "2008-10-21"),
    ("Oct 2008", "2008-10-01"),
    ("2008-10-21", "2008-10-21"),
    ("Coming soon", None),
    ("", None),
])
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_review_score_needs_ten_reviews():
    assert review_score(9, 0) is None
    assert review_score(7, 3) == 70
    assert review_score(2, 1) is None


def test_summarize_cuts_at_word_boundary():
    text = "word " * 100
    short = summarize(text.strip(), limit=22)
    assert short == "word word word word…"


def test_fix_header_splits_merged_column():
    assert fix_header(["AppID", "DiscountDLC count", "About the game"]) == [
        "AppID", "Discount", "DLC count", "About the game",
    ]
