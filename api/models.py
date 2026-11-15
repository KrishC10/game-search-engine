"""Request and response shapes for the API."""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

MAX_PAGE_SIZE = 50
MAX_RESULT_WINDOW = 10_000  # Elasticsearch's default index.max_result_window


class Mode(str, Enum):
    keyword = "keyword"
    semantic = "semantic"
    hybrid = "hybrid"


class Sort(str, Enum):
    relevance = "relevance"
    price_asc = "price_asc"
    price_desc = "price_desc"
    rating = "rating"
    newest = "newest"


class SearchParams(BaseModel):
    q: str = ""
    mode: Mode = Mode.hybrid
    genres: List[str] = Field(default_factory=list)
    price_min: Optional[float] = Field(None, ge=0)
    price_max: Optional[float] = Field(None, ge=0)
    min_score: Optional[int] = Field(None, ge=0, le=100)
    year_from: Optional[int] = Field(None, ge=1970, le=2100)
    year_to: Optional[int] = Field(None, ge=1970, le=2100)
    free_only: bool = False
    sort: Sort = Sort.relevance
    page: int = Field(1, ge=1)
    size: int = Field(20, ge=1, le=MAX_PAGE_SIZE)

    @model_validator(mode="after")
    def check_ranges(self) -> "SearchParams":
        if self.price_min is not None and self.price_max is not None and self.price_min > self.price_max:
            raise ValueError("price_min must not be greater than price_max")
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from must not be greater than year_to")
        if self.page * self.size > MAX_RESULT_WINDOW:
            raise ValueError(f"page * size must not exceed {MAX_RESULT_WINDOW}")
        self.q = self.q.strip()
        return self


class GameResult(BaseModel):
    app_id: str
    name: str
    short_description: Optional[str] = None
    genres: List[str] = []
    tags: List[str] = []
    price: Optional[float] = None
    is_free: Optional[bool] = None
    release_date: Optional[str] = None
    review_score: Optional[int] = None
    header_image: Optional[str] = None
    score: Optional[float] = None


class Bucket(BaseModel):
    key: str
    count: int


class PriceBucket(Bucket):
    # The price_min / price_max values that select exactly this bucket.
    min: Optional[float] = None
    max: Optional[float] = None


class Facets(BaseModel):
    genres: List[Bucket]
    price: List[PriceBucket]
    years: List[Bucket]


class SearchResponse(BaseModel):
    total: int
    took_ms: int
    page: int
    size: int
    mode: Mode  # the mode that actually ran (hybrid falls back to keyword without embeddings)
    results: List[GameResult]
    facets: Facets


class Suggestion(BaseModel):
    app_id: str
    name: str


class Clues(BaseModel):
    """What the system understood from a half-remembered description."""
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    genres: List[str] = []
    keywords: List[str] = []
    description: str
    platform_hints: List[str] = []


class FindRequest(BaseModel):
    memory: str = Field(min_length=10, max_length=1000)
    use_ai: bool = True


class FindResponse(BaseModel):
    clues: Clues
    query: str
    used_ai: bool  # false when use_ai was off or the LLM step failed and fell back to raw text
    semantic: bool  # false when the embedding model is unavailable (keyword list only)
    took_ms: int
    results: List[GameResult]
