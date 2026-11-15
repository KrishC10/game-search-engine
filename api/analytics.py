"""Search analytics: one document per search in the search-log index, for Kibana.

Logging runs after the response is sent and never fails a search.
No IP addresses or other personal data are stored.
"""
import logging
import os
from datetime import datetime, timezone

from elasticsearch import Elasticsearch

LOG_INDEX = os.getenv("ES_LOG_INDEX", "search-log")

MAPPING = {
    "dynamic": "strict",
    "properties": {
        "timestamp": {"type": "date"},
        "endpoint": {"type": "keyword"},
        "query": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 256}}},
        "mode": {"type": "keyword"},
        "genres": {"type": "keyword"},
        "sort": {"type": "keyword"},
        "page": {"type": "integer"},
        "total": {"type": "integer"},
        "zero_results": {"type": "boolean"},
        "took_ms": {"type": "integer"},
        "used_ai": {"type": "boolean"},
        "top_app_id": {"type": "keyword"},
    },
}

log = logging.getLogger("analytics")


def ensure_index(es: Elasticsearch) -> None:
    try:
        if not es.indices.exists(index=LOG_INDEX):
            es.indices.create(index=LOG_INDEX, mappings=MAPPING, settings={"number_of_replicas": 0})
    except Exception:
        log.warning("Could not create the %s index; search logging is off until it exists", LOG_INDEX)


def log_search(es: Elasticsearch, **event) -> None:
    event["timestamp"] = datetime.now(timezone.utc).isoformat()
    if "total" in event:
        event["zero_results"] = event["total"] == 0
    try:
        es.index(index=LOG_INDEX, document=event)
    except Exception:
        log.warning("Failed to log a search event", exc_info=True)
