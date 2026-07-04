from __future__ import annotations

from mcp.server.fastmcp import FastMCP

TOOL_NAMES = {
    "search_documents",
    "get_chunk_context",
    "list_documents",
    "get_sync_status",
}

mcp = FastMCP("OpenSQL AutoRAG Sync")


@mcp.tool()
def search_documents(query: str, top_k: int = 5) -> dict:
    return {"query": query, "top_k": top_k, "results": []}


@mcp.tool()
def get_chunk_context(chunk_id: str) -> dict:
    return {"chunk_id": chunk_id, "context": []}


@mcp.tool()
def list_documents() -> dict:
    return {"documents": []}


@mcp.tool()
def get_sync_status(document_id: str) -> dict:
    return {"document_id": document_id, "status": "unknown"}


if __name__ == "__main__":
    mcp.run()
