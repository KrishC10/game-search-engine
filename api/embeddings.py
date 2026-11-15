"""Sentence embeddings shared by ingestion and search (all-MiniLM-L6-v2, 384 dimensions).

sentence-transformers is imported lazily so the keyword-only parts of the
project (and the unit tests) run without it.
"""
import os
from functools import lru_cache
from typing import List, Sequence, Tuple

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DIMS = 384


class SemanticUnavailable(RuntimeError):
    """Raised when the embedding model cannot be loaded."""


@lru_cache(maxsize=1)
def _model():
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        raise SemanticUnavailable("semantic search needs the sentence-transformers package") from e
    return SentenceTransformer(MODEL_NAME)


def encode(texts: Sequence[str], batch_size: int = 64) -> List[List[float]]:
    # Texts longer than the model's input length (256 word pieces) are truncated by the model.
    vectors = _model().encode(list(texts), batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    return vectors.tolist()


@lru_cache(maxsize=2048)
def _encode_query(text: str) -> Tuple[float, ...]:
    return tuple(encode([text])[0])


def encode_query(text: str) -> List[float]:
    """Vector for a search query; cached because people repeat searches."""
    return list(_encode_query(text))
