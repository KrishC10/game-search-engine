"""FastAPI app: validates requests, runs searches, serves the web page.

Run from the repo root:
    uvicorn api.main:app --reload
"""
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, List

from elasticsearch import ConnectionError as ESConnectionError
from elasticsearch import ConnectionTimeout, NotFoundError
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.analytics import ensure_index, log_search
from api.embeddings import SemanticUnavailable
from api.es import INDEX, make_client
from api.find import find as run_find
from api.models import FindRequest, FindResponse, GameResult, SearchParams, SearchResponse, Suggestion
from api.ratelimit import RateLimiter
from api.search import SearchFailed, autocomplete as run_autocomplete, search as run_search, similar as run_similar

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
FIND_LIMIT_PER_MINUTE = 10


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.es = make_client()
    app.state.find_limiter = RateLimiter(limit=FIND_LIMIT_PER_MINUTE, window_seconds=60)
    ensure_index(app.state.es)
    yield
    app.state.es.close()


app = FastAPI(title="Tip-of-the-Tongue Game Finder", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_params(request: Request, exc: RequestValidationError):
    messages = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"] if part not in ("query", "path", "body"))
        messages.append(f"{field}: {error['msg']}" if field else error["msg"])
    return JSONResponse(status_code=400, content={"detail": "; ".join(messages)})


@app.exception_handler(ESConnectionError)
@app.exception_handler(ConnectionTimeout)
async def es_unavailable(request: Request, exc: Exception):
    return JSONResponse(status_code=503, content={"detail": "Search is unavailable: cannot reach Elasticsearch"})


@app.exception_handler(SemanticUnavailable)
async def semantic_unavailable(request: Request, exc: SemanticUnavailable):
    return JSONResponse(status_code=503, content={"detail": f"Semantic search is unavailable: {exc}"})


@app.exception_handler(SearchFailed)
async def search_failed(request: Request, exc: SearchFailed):
    return JSONResponse(status_code=502, content={"detail": f"Elasticsearch rejected the search: {exc}"})


@app.get("/search", response_model=SearchResponse)
def search(request: Request, params: Annotated[SearchParams, Query()], background: BackgroundTasks):
    result = run_search(request.app.state.es, params)
    background.add_task(
        log_search, request.app.state.es,
        endpoint="search", query=params.q, mode=result.mode.value, genres=params.genres,
        sort=params.sort.value, page=params.page, total=result.total, took_ms=result.took_ms,
        top_app_id=result.results[0].app_id if result.results else None,
    )
    return result


@app.post("/find", response_model=FindResponse)
def find(request: Request, body: FindRequest, background: BackgroundTasks):
    client_ip = request.client.host if request.client else "unknown"
    if not request.app.state.find_limiter.allow(client_ip):
        raise HTTPException(status_code=429, detail=f"Limit is {FIND_LIMIT_PER_MINUTE} requests a minute. Try again shortly.")
    result = run_find(request.app.state.es, body.memory, use_ai=body.use_ai)
    background.add_task(
        log_search, request.app.state.es,
        endpoint="find", query=body.memory, mode="hybrid_ai" if result.used_ai else "hybrid",
        total=len(result.results), took_ms=result.took_ms, used_ai=result.used_ai,
        top_app_id=result.results[0].app_id if result.results else None,
    )
    return result


@app.get("/autocomplete", response_model=List[Suggestion])
def autocomplete(request: Request, q: Annotated[str, Query(min_length=2, max_length=100)]):
    return run_autocomplete(request.app.state.es, q.strip())


@app.get("/games/{app_id}")
def get_game(request: Request, app_id: str):
    try:
        source = request.app.state.es.get(index=INDEX, id=app_id)["_source"]
    except NotFoundError:
        raise HTTPException(status_code=404, detail=f"No game with app_id {app_id}")
    # Vectors are 384 numbers each: useful to the engine, noise to API users.
    return {k: v for k, v in source.items() if not k.endswith("_vector")}


@app.get("/games/{app_id}/similar", response_model=List[GameResult])
def similar_games(request: Request, app_id: str, size: Annotated[int, Query(ge=1, le=20)] = 6):
    if not request.app.state.es.exists(index=INDEX, id=app_id):
        raise HTTPException(status_code=404, detail=f"No game with app_id {app_id}")
    return run_similar(request.app.state.es, app_id, size)


@app.get("/health")
def health(request: Request):
    es = request.app.state.es
    cluster = es.cluster.health()
    return {
        "api": "ok",
        "elasticsearch": cluster["status"],
        "index": INDEX,
        "documents": es.count(index=INDEX)["count"] if es.indices.exists(index=INDEX) else 0,
    }


# Mounted last so the API routes and /docs take precedence.
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
