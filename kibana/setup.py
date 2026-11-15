"""Create Kibana data views for the games and search-log indices.

Run once Kibana is up (docker compose up -d):
    python -m kibana.setup
Then build the dashboard in Kibana as described in the README.
"""
import os
import sys

import httpx

KIBANA_URL = os.getenv("KIBANA_URL", "http://localhost:5601")

DATA_VIEWS = [
    {"id": "search-log", "title": "search-log", "name": "Search log", "timeFieldName": "timestamp"},
    {"id": "games", "title": "games", "name": "Games"},
]


def main() -> None:
    headers = {"kbn-xsrf": "true"}
    auth = (os.environ["ES_USER"], os.environ["ES_PASSWORD"]) if os.getenv("ES_USER") else None
    for view in DATA_VIEWS:
        resp = httpx.post(
            f"{KIBANA_URL}/api/data_views/data_view",
            json={"data_view": view, "override": True},
            headers=headers,
            auth=auth,
            timeout=30,
        )
        if resp.status_code >= 400:
            sys.exit(f"Creating data view '{view['name']}' failed: {resp.status_code} {resp.text}")
        print(f"Data view ready: {view['name']}")


if __name__ == "__main__":
    main()
