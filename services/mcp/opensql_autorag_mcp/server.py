from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from opensql_autorag_api.db import get_connection
from opensql_autorag_api.embeddings import get_embedding_provider
from opensql_autorag_api.outline_access import resolver
from opensql_autorag_api.repository import Repository, SearchScope
from opensql_autorag_api.search import execute_search
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
def search_documents(query: str, top_k: int = 5, mode: str | None = None) -> dict:
    """Search the indexed documents.

    `mode` is `hybrid` (the default: meaning and wording together), `vector`
    for meaning alone, or `keyword` for literal wording -- useful when the query
    is an identifier, an error string, or a name that must match exactly.
    """
    scope, applied_scope = _scope()
    with get_connection() as connection:
        outcome = execute_search(
            Repository(connection),
            get_embedding_provider(),
            scope,
            applied_scope,
            query,
            top_k,
            mode,
        )
    return {
        "query": query,
        "top_k": top_k,
        "mode": outcome.mode,
        "embedding_model": outcome.embedding_model,
        "scope": outcome.scope,
        "results": _rows(outcome.rows),
        # An agent cannot read a server log. If the answer is empty because this
        # process is pointed at a model nothing was indexed with, that has to
        # travel back in the tool result or it is invisible.
        "warning": outcome.warning,
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
