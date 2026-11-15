# Tip-of-the-Tongue Game Finder

Describe a game you half-remember ("a fox in a ruined city, black and white, around 2015")
and it finds it. It searches about 80,000 Steam games with keyword, semantic and AI-assisted
search on Elasticsearch, and it measures how often it gets the right answer.

> Live demo: _coming soon_ · Screenshots: _add `docs/search.png` and `docs/find.png`_

## Results

Accuracy on tip-of-the-tongue requests with known answers (see [Evaluation](#evaluation)).

| Mode | Top-1 | Top-5 | Top-10 |
|---|---|---|---|
| Keyword | – | – | – |
| Semantic | – | – | – |
| Hybrid | – | – | – |
| Hybrid + AI query step | – | – | – |

Relevance test set (`tests/relevance_cases.json`, expected game in the top 3): _not run yet_.

## How it works

```
Steam CSV + reviews CSV → ingest → Elasticsearch ← FastAPI ← web page
                                        ↓
                                     Kibana (data, search analytics)
```

1. **Ingestion** (`ingest/`) cleans each game, keeps its 20 most helpful English reviews,
   embeds the description and the reviews, and bulk-loads everything into the `games` index.
2. **Elasticsearch** stores the games and runs every search. All ranking logic lives in the
   queries the API sends (`api/queries.py`).
3. **The API** (`api/`, FastAPI) validates requests, builds Query DSL, fuses result lists and
   returns clean JSON. The page never talks to Elasticsearch directly.
4. **The page** (`web/`) is plain HTML, CSS and JavaScript.

### Search modes

| Mode | What runs |
|---|---|
| Keyword | `multi_match` over name ×3, tags ×2, short description ×1.5, description ×1, with typo tolerance, a phrase and exact-title boost, and a small popularity boost |
| Semantic | k-nearest-neighbour search on description vectors (all-MiniLM-L6-v2, 384 dimensions), filters applied inside the kNN search |
| Hybrid (default) | Keyword and semantic lists merged with Reciprocal Rank Fusion |
| Find (half-remember) | An LLM turns the memory into clues and a clean query, then keyword (including review text) + description vectors + review vectors are fused |

### Tip-of-the-tongue flow (`POST /find`)

1. **Understand.** The LLM (`api/understand.py`) returns structured JSON: year range, Steam
   genres, concrete keywords, a clean one-sentence description and platform hints. The output
   is validated (known genres only, plausible years, bounded lists); on any failure the raw
   text is used instead.
2. **Search.** One multi-search runs three lists: keywords across names, tags, descriptions and
   review snippets; nearest neighbours on description vectors; nearest neighbours on review vectors.
3. **Filter softly.** Year (widened by ±3) and genre clues are boosts, never filters, because
   memories are often wrong.
4. **Return** the top 10 with the extracted clues, so the user can see what was understood.

The LLM never picks the answer. It only rewrites the query; Elasticsearch does the finding,
which keeps results explainable and testable.

## Run it locally

Needs Docker and Python 3.11+.

```bash
docker compose up -d
```

```bash
python3.11 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Download the data into `data/` (it is git-ignored):

- **Games:** a Steam games dataset from Kaggle with name, description, genres, tags, price,
  release date and review counts (for example the FronkonGames "Steam Games Dataset"), saved as
  `data/games.csv`. Check its column names against `COLUMNS` in `ingest/clean.py`.
- **Reviews (optional):** a Steam reviews dataset from Kaggle saved as `data/reviews.csv`.
  Check its columns against `COLUMNS` in `ingest/reviews.py`.

Check each dataset's licence before using it.

```bash
python -m ingest.ingest --csv data/games.csv --reviews data/reviews.csv --recreate
```

Add `--no-embed` for a fast keyword-only load, or `--limit 2000` to experiment on a sample.
The log ends with how many rows were skipped and why.

```bash
uvicorn api.main:app --reload
```

Open http://localhost:8000 for the page and http://localhost:8000/docs for the API docs.

### The AI query step

Set `LLM_PROVIDER` in `.env`:

- `anthropic` (default): needs `ANTHROPIC_API_KEY`. The model is `ANTHROPIC_MODEL`
  (default `claude-opus-5-5`), run at low effort with structured outputs. If a safety
  classifier declines a request, the API retries on Anthropic's recommended fallback model.
- `ollama`: free and local. Install Ollama, run `ollama pull llama3.1:8b`, and set `OLLAMA_MODEL`.
- `none`: skip the AI step; `/find` searches the raw text.

`/find` is limited to 10 requests a minute per IP because each call can cost an LLM request.

## API

| Endpoint | Parameters | Returns |
|---|---|---|
| `GET /search` | `q`, `mode` (keyword, semantic, hybrid), `genres` (repeatable), `price_min`, `price_max`, `min_score`, `year_from`, `year_to`, `free_only`, `sort`, `page`, `size` (max 50) | `total`, `took_ms`, `mode`, `results`, `facets` |
| `POST /find` | JSON body: `memory` (10–1,000 characters), `use_ai` (default true) | `clues`, `query`, `used_ai`, `semantic`, `took_ms`, top 10 `results` |
| `GET /games/{app_id}` | – | One game |
| `GET /games/{app_id}/similar` | `size` (default 6) | Similar games |
| `GET /autocomplete` | `q` (2+ characters) | Up to 8 names |
| `GET /health` | – | API status, cluster health, document count |

Invalid parameters return 400 with a message, Elasticsearch being down returns 503, and an
over-limit `/find` returns 429.

## Evaluation

`eval/requests.json` holds requests with known answers: `id`, `request`, `answer_app_id`,
`answer_name`, `source`, `url`. The 12 entries marked `"source": "example"` are placeholders
to prove the harness works. Replace them with 100–200 real requests, for example solved posts
from r/tipofmyjoystick (check Reddit's terms first) or requests written by friends, so they are
not all in one person's words.

```bash
python -m eval.eval --check
```

```bash
python -m eval.eval --split tune --note "keyword baseline"
```

Each request is assigned to `tune` (about 70%) or `holdout` (about 30%) by a hash of its id.
Tune on `tune`; run `--split holdout` once at the end. Every run is appended to
`eval/results.md` with the date and note, so improvements are recorded with evidence.

```bash
python -m tests.relevance --mode hybrid
```

## Design decisions

- **Explicit mapping, `dynamic: strict`.** Dynamic mapping guesses wrong (price as text);
  strict mode turns a stray field into an error instead of a silent new field.
- **English analyzer on text, keywords for facets.** `name` is analysed for search, with
  `name.keyword` (lowercase normaliser) for exact matches and sorting, and
  `name.suggest` (`search_as_you_type`) for autocomplete.
- **`scaled_float` for price.** Stored as whole cents: exact for filters, compact on disk.
- **Title matches first.** A `match_phrase` boost and an exact `name.keyword` boost sit on top
  of the weighted `multi_match`, so "portal 2" ranks Portal 2 above its soundtrack.
- **Popularity as a tie-breaker, not the ranking.** `function_score` multiplies the text score by
  `1 + 0.1·log10(1 + reviews) + 0.003·score`, at most about ×2, so relevance still decides.
- **Disjunctive facets with `post_filter`.** Genre, price and year filters run after the
  aggregations, and each facet is filtered by every *other* facet. Ticking "Action" still shows
  counts for the genres you could add. Review-score and free-to-play filters stay in the query's
  filter context, which is cached and does not affect scores.
- **Filters inside kNN.** Filtering during the nearest-neighbour search returns k games that all
  match, instead of finding k and discarding some.
- **RRF in Python.** Reciprocal Rank Fusion (`1 / (60 + rank)`) needs no score normalisation and no
  paid licence, and the code is easy to test. Hybrid results are sorted in Python on the fused list.
- **Reviews as evidence.** People remember moments ("the creepy doll"), which players write in
  reviews far more than store pages do, so review text is searched by keyword and by vector.
- **The LLM only rewrites.** It extracts clues; search finds the answer. Clues are boosts, not
  filters, because a remembered "2015" may really be 2012.
- **Refresh off during bulk load.** `refresh_interval: -1` while loading, one refresh at the end.
- **Graceful fallbacks.** No embedding model: hybrid becomes keyword (the response says so).
  LLM failure: the raw text is searched. Logging failures never fail a search.

## Search analytics in Kibana

Every search and find is logged to the `search-log` index (query, mode, result count,
zero-result flag, latency, top result). No IP addresses are stored.

```bash
python -m kibana.setup
```

This creates the data views. Then in Kibana (http://localhost:5601), build a dashboard with:
searches over time, top queries (`query.keyword`), zero-result queries
(`zero_results: true`), latency (median and 95th percentile of `took_ms`), and the share of
`/find` calls where the AI step ran (`used_ai`).

## Tests

```bash
pytest
```

Unit tests cover every cleaning rule, the Query DSL for each filter combination and search
mode, RRF and clue boosts, LLM output validation and fallback, review cleaning, rate limiting,
and the API's 400/429/503 responses. None of them need a running cluster or an API key.

## Deployment

Keep credentials in environment variables only. For a short demo, Elastic Cloud's free trial;
longer term, a small cloud server running this Docker setup (roughly $5–10 a month) or a managed
Elasticsearch host. Point `ES_URL`, `ES_USER` and `ES_PASSWORD` at the cluster and run
`uvicorn api.main:app --host 0.0.0.0`.

## Repo layout

```
api/        FastAPI app, Query DSL, search modes, find flow, LLM step, analytics
ingest/     mapping, cleaning, reviews, embeddings, bulk load
web/        the page
eval/       evaluation requests, script and recorded results
kibana/     data view setup
tests/      unit tests and relevance cases
```
