from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
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


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> dict:
    provider = HashEmbeddingProvider(dimension=settings.embedding_dimension)
    query_embedding = provider.embed(query)
    with get_connection() as connection:
        results = Repository(connection).search_chunks(query_embedding, top_k)
    return {"query": query, "top_k": top_k, "results": _rows(results)}


@mcp.tool()
def get_chunk_context(chunk_id: str) -> dict:
    with get_connection() as connection:
        context = Repository(connection).get_chunk_context(UUID(chunk_id))
    return {"chunk_id": chunk_id, "context": _rows(context)}


@mcp.tool()
def list_documents() -> dict:
    with get_connection() as connection:
        documents = Repository(connection).list_documents()
    return {"documents": _rows(documents)}


@mcp.tool()
def get_sync_status(document_id: str) -> dict:
    with get_connection() as connection:
        status = Repository(connection).latest_sync_status(UUID(document_id))
    return {"document_id": document_id, "status": _row(status) if status else None}


if __name__ == "__main__":
    mcp.run()
