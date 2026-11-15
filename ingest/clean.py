"""Cleaning rules: turn one raw CSV row into a game document for the index."""
import csv
import html
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

# Logical field -> candidate CSV column names; the first non-empty one wins.
# Check these against the real header after downloading a dataset.
COLUMNS = {
    "app_id": ["AppID", "appid", "app_id", "steam_appid"],
    "name": ["Name", "name"],
    "short_description": ["Short description", "short_description"],
    "description": ["About the game", "about_the_game", "detailed_description", "description"],
    "genres": ["Genres", "genres"],
    "tags": ["Tags", "tags"],
    "developer": ["Developers", "developers", "developer"],
    "price": ["Price", "price"],
    "release_date": ["Release date", "release_date"],
    "positive": ["Positive", "positive"],
    "negative": ["Negative", "negative"],
    "header_image": ["Header image", "header_image"],
}

DATE_FORMATS = ["%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b, %Y", "%d %B, %Y", "%b %Y", "%B %Y"]
MIN_REVIEWS = 10
SHORT_DESCRIPTION_CHARS = 300

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def read_rows(path: Path) -> Iterator[Dict[str, str]]:
    """Yield CSV rows as dicts, fixing known header problems first."""
    csv.field_size_limit(2**31 - 1)  # descriptions can be very long
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = fix_header(next(reader))
        for values in reader:
            yield dict(zip(header, values))


def fix_header(header: List[str]) -> List[str]:
    # The popular FronkonGames Steam CSV labels two columns as one
    # ("DiscountDLC count"), which shifts every later column by one.
    fixed = []
    for column in header:
        if column == "DiscountDLC count":
            fixed += ["Discount", "DLC count"]
        else:
            fixed.append(column.strip())
    return fixed


def pick(row: Dict[str, str], field: str) -> str:
    for column in COLUMNS[field]:
        value = row.get(column)
        if value and value.strip():
            return value.strip()
    return ""


def strip_html(text: str) -> str:
    text = TAG_RE.sub(" ", text)
    return SPACE_RE.sub(" ", html.unescape(text)).strip()


def split_list(value: str) -> List[str]:
    """'Action,Indie, Action' -> ['Action', 'Indie'] (order kept, duplicates dropped)."""
    items = [item.strip() for item in value.split(",")]
    return list(dict.fromkeys(item for item in items if item))


def parse_price(value: str) -> Optional[float]:
    value = value.strip().lower().replace("$", "").replace(",", "")
    if value in ("free", "free to play", "free-to-play"):
        return 0.0
    try:
        price = round(float(value), 2)
    except ValueError:
        return None
    return price if price >= 0 else None


def parse_date(value: str) -> Optional[str]:
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_int(value: str) -> int:
    try:
        return int(float(value))
    except ValueError:
        return 0


def review_score(positive: int, negative: int) -> Optional[int]:
    total = positive + negative
    if total < MIN_REVIEWS:
        return None
    return round(positive / total * 100)


def summarize(text: str, limit: int = SHORT_DESCRIPTION_CHARS) -> str:
    """Fallback short description: cut the full description at a word boundary."""
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def clean_row(row: Dict[str, str]) -> Tuple[Optional[dict], Optional[str]]:
    """Return (document, None), or (None, reason) when the row must be skipped."""
    app_id = pick(row, "app_id")
    name = html.unescape(pick(row, "name"))
    description = strip_html(pick(row, "description"))
    short_description = strip_html(pick(row, "short_description"))

    if not app_id:
        return None, "missing app_id"
    if not name:
        return None, "missing name"
    if not description and not short_description:
        return None, "missing description"

    positive = parse_int(pick(row, "positive"))
    negative = parse_int(pick(row, "negative"))
    price = parse_price(pick(row, "price"))
    release_date = parse_date(pick(row, "release_date"))

    doc = {
        "app_id": app_id,
        "name": name,
        "short_description": short_description or summarize(description),
        "description": description or short_description,
        "genres": split_list(pick(row, "genres")),
        "tags": split_list(pick(row, "tags")),
        "developer": split_list(pick(row, "developer")),
        "price": price,
        "is_free": price == 0 if price is not None else None,
        "release_date": release_date,
        "release_year": int(release_date[:4]) if release_date else None,
        "review_score": review_score(positive, negative),
        "review_count": positive + negative,
        "header_image": pick(row, "header_image") or None,
    }
    return {key: value for key, value in doc.items() if value is not None}, None


def clean_rows(rows: Iterable[Dict[str, str]], stats: Counter) -> Iterator[dict]:
    """Clean every row, drop duplicates by app_id, and count what happened in stats."""
    seen = set()
    for row in rows:
        doc, reason = clean_row(row)
        if doc is None:
            stats[f"skipped: {reason}"] += 1
            continue
        if doc["app_id"] in seen:
            stats["skipped: duplicate app_id"] += 1
            continue
        seen.add(doc["app_id"])
        for field in ("price", "release_date", "review_score"):
            if field not in doc:
                stats[f"field left empty: {field}"] += 1
        stats["cleaned"] += 1
        yield doc
