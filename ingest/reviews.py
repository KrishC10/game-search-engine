"""Review cleaning: keep each game's most helpful English reviews.

People remember games by moments and feelings, which players write in reviews
far more often than official store descriptions do.
"""
import csv
import heapq
import itertools
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

# Logical field -> candidate CSV column names, as in clean.COLUMNS.
COLUMNS = {
    "app_id": ["app_id", "appid", "AppID", "steam_appid"],
    "text": ["review", "review_text", "text", "content"],
    "votes": ["votes_helpful", "review_votes", "votes_up", "helpful"],
    "language": ["language"],
}

REVIEWS_PER_GAME = 20
MIN_WORDS = 20
MAX_REVIEW_CHARS = 1500

LINK_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
MARKUP_RE = re.compile(r"\[/?[a-z0-9*]+(=[^\]]*)?\]|<[^>]+>", re.IGNORECASE)  # Steam BBCode and HTML
SPACE_RE = re.compile(r"\s+")
ENGLISH_WORDS = {"the", "and", "is", "it", "to", "of", "a", "this", "game", "you", "i", "that", "with", "for"}


def column(header: List[str], field: str) -> Optional[str]:
    return next((c for c in COLUMNS[field] if c in header), None)


def clean_review(text: str) -> Optional[str]:
    """Strip links and markup; return None for reviews too short to say anything."""
    text = MARKUP_RE.sub(" ", LINK_RE.sub(" ", text))
    text = SPACE_RE.sub(" ", text).strip()
    if len(text.split()) < MIN_WORDS:
        return None
    return text[:MAX_REVIEW_CHARS]


def looks_english(text: str) -> bool:
    """Cheap check for datasets without a language column."""
    words = text.lower().split()
    ascii_share = sum(ch.isascii() for ch in text) / max(len(text), 1)
    return ascii_share > 0.95 and sum(w in ENGLISH_WORDS for w in words) >= 3


def parse_votes(value: str) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def load_top_reviews(path: Path, stats: Counter, per_game: int = REVIEWS_PER_GAME) -> Dict[str, List[str]]:
    """Stream a reviews CSV and keep the per_game most helpful English reviews per app_id."""
    csv.field_size_limit(2**31 - 1)
    heaps: Dict[str, list] = defaultdict(list)
    tiebreak = itertools.count()
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        app_col, text_col = column(header, "app_id"), column(header, "text")
        votes_col, lang_col = column(header, "votes"), column(header, "language")
        if not app_col or not text_col:
            raise ValueError(f"Reviews CSV needs an app id and a review text column; found {header}")
        for row in reader:
            stats["reviews read"] += 1
            if lang_col and row.get(lang_col, "").strip().lower() != "english":
                stats["reviews skipped: not english"] += 1
                continue
            text = clean_review(row.get(text_col) or "")
            if text is None:
                stats["reviews skipped: under 20 words"] += 1
                continue
            if not lang_col and not looks_english(text):
                stats["reviews skipped: not english"] += 1
                continue
            app_id = (row.get(app_col) or "").strip()
            votes = parse_votes(row.get(votes_col, "0") if votes_col else "0")
            heap = heaps[app_id]
            item = (votes, next(tiebreak), text)
            if len(heap) < per_game:
                heapq.heappush(heap, item)
            else:
                heapq.heappushpop(heap, item)  # drops the least helpful
    stats["games with reviews"] = len(heaps)
    return {app_id: [text for _, _, text in sorted(heap, reverse=True)] for app_id, heap in heaps.items()}
