"""Elasticsearch client built from environment variables (see .env.example)."""
import os

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()

INDEX = os.getenv("ES_INDEX", "games")


def make_client() -> Elasticsearch:
    url = os.getenv("ES_URL", "http://localhost:9200")
    user = os.getenv("ES_USER")
    password = os.getenv("ES_PASSWORD")
    auth = (user, password) if user and password else None
    return Elasticsearch(url, basic_auth=auth, request_timeout=30)
