from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from opensql_autorag.embeddings import EmbeddingProvider, create_embedding_provider

from opensql_autorag_api.settings import settings


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """The configured provider, built once per process.

    Caching matters for `sentence-transformers`: constructing the provider loads
    the model, which is far too expensive to repeat per request.
    """
    return create_embedding_provider(
        provider=settings.embedding_provider,
        model_name=settings.embedding_model,
        dimension=settings.embedding_dimension,
    )


class _CoverageSource(Protocol):
    def embedding_coverage(self) -> list[dict]: ...


def embedding_mismatch(repository: _CoverageSource, embedding_model_id: int) -> str | None:
    """Why a search against this model can only come back empty, if it can.

    Dimension validation does not catch the mismatch that actually happens.
    `hash` and `intfloat/multilingual-e5-small` are both 384 dimensions, so a
    process configured with one while the index was built with the other embeds
    the query fine, fits the column fine, and matches nothing — a 200 with an
    empty list and no reason given, which reads as "no results for that query".

    Returns None when nothing is indexed yet: an empty index is not a
    misconfiguration, it is an empty index.
    """
    coverage = repository.embedding_coverage()
    if not coverage:
        return None
    if any(int(row["embedding_model_id"]) == embedding_model_id for row in coverage):
        return None

    indexed = ", ".join(
        f"{row['provider']}/{row['model_name']} ({row['chunk_count']} chunks)" for row in coverage
    )
    configured = f"{settings.embedding_provider}/{get_embedding_provider().model_name}"
    return (
        f"nothing is embedded with {configured}, so this search can only return "
        f"nothing. The index holds: {indexed}. Point AUTORAG_EMBEDDING_PROVIDER "
        "and AUTORAG_EMBEDDING_MODEL at the model the worker indexed with, or "
        "re-index with the current one."
    )
