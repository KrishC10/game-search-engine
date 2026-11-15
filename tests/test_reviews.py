import csv
from collections import Counter

from ingest.reviews import clean_review, load_top_reviews, looks_english

LONG = "This game is one of the best things I have played and the story with the creepy doll stayed with me for weeks after"


def test_clean_review_strips_links_and_markup():
    text = clean_review(f"[b]Wow[/b] <i>really</i> see https://example.com/x and www.example.org {LONG}")
    assert "http" not in text and "www." not in text
    assert "[b]" not in text and "<i>" not in text
    assert text.startswith("Wow really see and")


def test_clean_review_drops_short_reviews():
    assert clean_review("10/10 would play again") is None


def test_looks_english():
    assert looks_english(LONG)
    assert not looks_english("Это лучшая игра, в которую я играл, и история осталась со мной")


def write_csv(path, rows, header):
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def test_load_top_reviews_keeps_most_helpful_english(tmp_path):
    path = tmp_path / "reviews.csv"
    write_csv(path, [
        ["1", f"low votes {LONG}", "1", "english"],
        ["1", f"high votes {LONG}", "50", "english"],
        ["1", f"middle votes {LONG}", "10", "english"],
        ["1", f"german {LONG}", "99", "german"],
        ["2", "too short", "5", "english"],
    ], ["app_id", "review", "votes_helpful", "language"])
    stats = Counter()
    reviews = load_top_reviews(path, stats, per_game=2)
    assert [r.split()[0] for r in reviews["1"]] == ["high", "middle"]
    assert "2" not in reviews
    assert stats["reviews skipped: not english"] == 1
    assert stats["reviews skipped: under 20 words"] == 1

