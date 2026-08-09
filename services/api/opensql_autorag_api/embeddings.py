from __future__ import annotations

from functools import lru_cache

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
