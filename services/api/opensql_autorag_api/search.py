"""Running one search, shared by the REST API and the MCP server.

Both surfaces have to answer identically -- an agent and a person asking the
same question of the same wiki should not get different results because the
retrieval mode was wired up twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opensql_autorag.embeddings import EmbeddingProvider

from opensql_autorag_api.embeddings import embedding_mismatch
from opensql_autorag_api.repository import Repository, SearchScope
from opensql_autorag_api.settings import settings

MODES = ("hybrid", "vector", "keyword")


@dataclass(frozen=True)
class SearchOutcome:
    rows: list[dict]
    mode: str
    embedding_model: str | None = None
    warning: str | None = None
    scope: dict = field(default_factory=dict)


def resolve_mode(requested: str | None) -> str:
    mode = (requested or settings.search_mode).strip().lower()
    if mode not in MODES:
        raise ValueError(f"unknown search mode {mode!r} (expected one of {', '.join(MODES)})")
    return mode


def execute_search(
    repository: Repository,
    provider: EmbeddingProvider,
    scope: SearchScope,
    applied_scope: dict,
    query: str,
    top_k: int,
    mode: str | None = None,
) -> SearchOutcome:
    """Retrieve for one query, in whichever mode is asked for.

    The keyword arm needs no embedding at all, so that mode never resolves a
    model and never reports one -- a keyword search is not affected by the model
    the index was built with, and saying otherwise would be noise.
    """
    resolved = resolve_mode(mode)

    if resolved == "keyword":
        rows = repository.search_chunks_keyword(query, top_k, scope)
        return SearchOutcome(rows=rows, mode=resolved, scope=applied_scope)

    query_embedding = provider.embed(query, role="query")
    embedding_model_id = repository.resolve_embedding_model_id(
        provider=settings.embedding_provider,
        model_name=provider.model_name,
        dimension=provider.dimension,
    )
    if resolved == "vector":
        rows = repository.search_chunks(query_embedding, top_k, embedding_model_id, scope)
    else:
        rows = repository.search_chunks_hybrid(
            query, query_embedding, top_k, embedding_model_id, scope
        )

    # Only worth resolving when there is nothing to show: an empty result is the
    # one case a caller cannot tell apart from a misconfiguration. Under hybrid
    # the keyword arm can still answer, so a mismatch there is a degraded search
    # rather than a dead one -- still worth saying, and still only when it shows.
    warning = embedding_mismatch(repository, embedding_model_id) if not rows else None
    return SearchOutcome(
        rows=rows,
        mode=resolved,
        embedding_model=f"{settings.embedding_provider}/{provider.model_name}",
        warning=warning,
        scope=applied_scope,
    )
