from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from opensql_autorag_api.db import get_connection
from opensql_autorag_api.embeddings import get_embedding_provider
from opensql_autorag_api.outline_access import resolver
from opensql_autorag_api.repository import Repository, SearchScope
from opensql_autorag_api.settings import settings

TOOL_NAMES = {
    "search_documents",
    "get_chunk_context",
    "list_documents",
    "get_sync_status",
}

mcp = FastMCP("OpenSQL AutoRAG Sync")


def _json_safe(value: object) -> object:
    if isinstance(value, UUID | datetime):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row(row: dict) -> dict:
    return {key: _json_safe(value) for key, value in row.items()}


def _rows(rows: list[dict]) -> list[dict]:
    return [_row(row) for row in rows]


def _scope() -> tuple[SearchScope, dict]:
    """What the user this server runs for is allowed to search.

    The server speaks stdio to a single user, so their Outline token comes from
    AUTORAG_OUTLINE_USER_TOKEN rather than from a request. Without one, only
    documents uploaded straight into AutoRAG are in scope -- wiki content is not
    served to an unidentified caller.
    """
    if not settings.outline_user_token:
        return SearchScope.local_only(), {"outline_user": None, "collection_count": 0}
    identity = resolver.resolve(settings.outline_user_token)
    return identity.scope(), {
        "outline_user": identity.user_id,
        "collection_count": len(identity.collection_ids),
    }


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> dict:
    scope, applied_scope = _scope()
    provider = get_embedding_provider()
    query_embedding = provider.embed(query)
    with get_connection() as connection:
        repository = Repository(connection)
        embedding_model_id = repository.resolve_embedding_model_id(
            provider=settings.embedding_provider,
            model_name=provider.model_name,
            dimension=provider.dimension,
        )
        results = repository.search_chunks(query_embedding, top_k, embedding_model_id, scope)
    return {
        "query": query,
        "top_k": top_k,
        "embedding_model": f"{settings.embedding_provider}/{provider.model_name}",
        "scope": applied_scope,
        "results": _rows(results),
    }


@mcp.tool()
def get_chunk_context(chunk_id: str) -> dict:
    scope, _ = _scope()
    with get_connection() as connection:
        context = Repository(connection).get_chunk_context(UUID(chunk_id), scope)
    return {"chunk_id": chunk_id, "context": _rows(context)}


@mcp.tool()
def list_documents() -> dict:
    scope, _ = _scope()
    with get_connection() as connection:
        documents = Repository(connection).list_documents(scope)
    return {"documents": _rows(documents)}


@mcp.tool()
def get_sync_status(document_id: str) -> dict:
    scope, _ = _scope()
    document = UUID(document_id)
    with get_connection() as connection:
        repository = Repository(connection)
        if not repository.document_in_scope(document, scope):
            return {"document_id": document_id, "status": None}
        status = repository.latest_sync_status(document)
    return {"document_id": document_id, "status": _row(status) if status else None}


if __name__ == "__main__":
    mcp.run()
