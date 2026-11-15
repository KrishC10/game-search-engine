"""Create the games index with the explicit mapping and bulk-load the cleaned CSV.

Usage (from the repo root):
    python -m ingest.ingest --csv data/games.csv --reviews data/reviews.csv --recreate
    python -m ingest.ingest --csv data/games.csv --recreate --no-embed   # keyword only, much faster
"""
import argparse
import itertools
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Iterator, List

from elasticsearch import Elasticsearch, helpers

from api.es import INDEX, make_client
from ingest.clean import clean_rows, read_rows
from ingest.reviews import load_top_reviews

MAPPING_PATH = Path(__file__).with_name("mapping.json")
CHUNK_SIZE = 500
EMBED_BATCH = 256

log = logging.getLogger("ingest")


def create_index(es: Elasticsearch, index: str, recreate: bool) -> None:
    if es.indices.exists(index=index):
        if not recreate:
            sys.exit(f"Index '{index}' already exists. Pass --recreate to drop and rebuild it.")
        log.info("Deleting existing index '%s'", index)
        es.indices.delete(index=index)
    mapping = json.loads(MAPPING_PATH.read_text())
    es.indices.create(index=index, settings=mapping["settings"], mappings=mapping["mappings"])
    log.info("Created index '%s' from %s", index, MAPPING_PATH.name)


def description_text(doc: dict) -> str:
    """What the description vector represents: name, short description and tags.

    The name is included so semantic search also understands title queries; without it,
    fusion lets games that match both lists loosely outrank an exact title hit.
    """
    return f"{doc['name']}. {doc['short_description']} Tags: {', '.join(doc.get('tags', []))}"


def attach_reviews(docs: Iterable[dict], reviews: Dict[str, List[str]], stats: Counter) -> Iterator[dict]:
    for doc in docs:
        snippets = reviews.get(doc["app_id"])
        if snippets:
            doc["review_snippets"] = snippets
            stats["games given review snippets"] += 1
        yield doc


def attach_vectors(docs: Iterable[dict]) -> Iterator[dict]:
    """Embed in batches: one model call per EMBED_BATCH games is far faster than one per game."""
    from api.embeddings import encode

    docs = iter(docs)
    while True:
        batch = list(itertools.islice(docs, EMBED_BATCH))
        if not batch:
            return
        for doc, vector in zip(batch, encode([description_text(d) for d in batch])):
            doc["description_vector"] = vector
        with_reviews = [d for d in batch if d.get("review_snippets")]
        if with_reviews:
            # Games with no English reviews keep their description vector only.
            texts = [" ".join(d["review_snippets"]) for d in with_reviews]
            for doc, vector in zip(with_reviews, encode(texts)):
                doc["review_vector"] = vector
        yield from batch


def load(es: Elasticsearch, index: str, docs: Iterable[dict]) -> Counter:
    results = Counter()
    actions = ({"_index": index, "_id": doc["app_id"], "_source": doc} for doc in docs)
    # Refreshing during a bulk load wastes work; turn it off and refresh once at the end.
    es.indices.put_settings(index=index, settings={"index": {"refresh_interval": "-1"}})
    try:
        for ok, item in helpers.streaming_bulk(es, actions, chunk_size=CHUNK_SIZE, raise_on_error=False):
            if ok:
                results["indexed"] += 1
                if results["indexed"] % 5_000 == 0:
                    log.info("Indexed %d games", results["indexed"])
            else:
                results["failed"] += 1
                if results["failed"] <= 5:
                    log.warning("Bulk failure: %s", item)
    finally:
        es.indices.put_settings(index=index, settings={"index": {"refresh_interval": "1s"}})
        es.indices.refresh(index=index)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", type=Path, default=Path("data/games.csv"))
    parser.add_argument("--reviews", type=Path, help="optional Steam reviews CSV for review snippets")
    parser.add_argument("--index", default=INDEX)
    parser.add_argument("--recreate", action="store_true", help="drop the index first if it exists")
    parser.add_argument("--no-embed", action="store_true", help="skip vectors (keyword search only)")
    parser.add_argument("--limit", type=int, help="only load the first N rows (for quick experiments)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # The clients log every HTTP request at INFO, which buries the progress lines.
    for noisy in ("elastic_transport", "httpx", "sentence_transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    for path in (args.csv, args.reviews):
        if path and not path.exists():
            sys.exit(f"File not found: {path}. Download the datasets into data/ first (see README).")

    stats = Counter()
    reviews: Dict[str, List[str]] = {}
    if args.reviews:
        log.info("Reading reviews from %s (large files take a few minutes)", args.reviews)
        reviews = load_top_reviews(args.reviews, stats)

    es = make_client()
    create_index(es, args.index, args.recreate)

    docs = clean_rows(itertools.islice(read_rows(args.csv), args.limit), stats)
    docs = attach_reviews(docs, reviews, stats)
    if not args.no_embed:
        docs = attach_vectors(docs)
    results = load(es, args.index, docs)

    for reason, count in sorted(stats.items()):
        log.info("%-36s %d", reason, count)
    log.info("Indexed %d, failed %d", results["indexed"], results["failed"])
    log.info("Documents in '%s': %d", args.index, es.count(index=args.index)["count"])


if __name__ == "__main__":
    main()
