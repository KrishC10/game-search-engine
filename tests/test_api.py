import pytest
from elasticsearch import ConnectionError as ESConnectionError
from fastapi.testclient import TestClient

import api.find
from api.embeddings import SemanticUnavailable
from api.main import FIND_LIMIT_PER_MINUTE, app


class DownCluster:
    """Stands in for an Elasticsearch client whose cluster is unreachable."""

    def _down(self, *args, **kwargs):
        raise ESConnectionError("connection refused")

    search = msearch = exists = index = _down

    def close(self):
        pass


def no_embeddings(text):
    raise SemanticUnavailable("not installed in tests")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.find, "encode_query", no_embeddings)
    with TestClient(app) as c:
        app.state.es = DownCluster()
        yield c


@pytest.mark.parametrize("query", [
    "size=51",
    "price_min=30&price_max=10",
    "sort=popularity",
    "page=0",
    "mode=fuzzy",
])
def test_invalid_search_params_return_400(client, query):
    resp = client.get(f"/search?{query}")
    assert resp.status_code == 400
    assert resp.json()["detail"]


def test_elasticsearch_down_returns_503(client):
    assert client.get("/search?q=portal&mode=keyword").status_code == 503


def test_autocomplete_needs_two_characters(client):
    assert client.get("/autocomplete?q=a").status_code == 400


@pytest.mark.parametrize("body", [
    {"memory": "too short"},
    {"memory": "x" * 1001},
    {},
])
def test_find_validates_memory_length(client, body):
    assert client.post("/find", json=body).status_code == 400


def test_find_is_rate_limited(client):
    body = {"memory": "a fox in a ruined city, black and white", "use_ai": False}
    statuses = [client.post("/find", json=body).status_code for _ in range(FIND_LIMIT_PER_MINUTE + 1)]
    assert statuses[:-1] == [503] * FIND_LIMIT_PER_MINUTE  # allowed through, then ES is down
    assert statuses[-1] == 429


def test_web_page_served(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Game Finder" in resp.text
