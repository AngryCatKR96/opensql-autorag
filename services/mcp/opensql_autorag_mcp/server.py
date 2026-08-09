"""The MCP face of AutoRAG, for an agent running on a developer's own machine.

This process is a translator, not a search engine. It turns tool calls into HTTP
requests to the AutoRAG API and hands the answer back unchanged; the API resolves
the caller's Outline access, applies the permission filter in SQL, and embeds the
query. That split is what makes it safe to hand out: it runs under one developer,
carries only that developer's own Outline token, and needs neither a database
credential nor a copy of the embedding model to do its job. A filter enforced on
the server cannot be queried around by the client that asks.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
from mcp.server.fastmcp import FastMCP
from opensql_autorag_api.settings import settings

TOOL_NAMES = {
    "search_documents",
    "get_chunk_context",
    "list_documents",
    "get_sync_status",
}

mcp = FastMCP("OpenSQL AutoRAG Sync")


class ApiError(RuntimeError):
    """The AutoRAG API could not be reached, or refused the request.

    Raised rather than returned as an empty result. An agent shown zero results
    reads that as "the wiki has nothing on this" and answers from its own guess;
    it has no server log to check, so the difference has to arrive as an error.
    """


def _client() -> httpx.Client:
    """A client per call, so a changed setting takes effect without a restart."""
    return httpx.Client(
        base_url=settings.api_base_url.rstrip("/"),
        timeout=settings.api_timeout_seconds,
    )


def _identified(chunk_or_document_id: str, field: str) -> str:
    """Ids reach this process as free text and are spliced into a URL path."""
    try:
        return str(UUID(chunk_or_document_id))
    except ValueError as exc:
        raise ValueError(f"{field} is not a UUID: {chunk_or_document_id!r}") from exc


def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Ask the API as the developer this server was launched by.

    The token travels in the header the API already reads from machine callers,
    so this path resolves to exactly the same scope the console would get for
    the same person. Sending no token is meaningful rather than an error: the
    API then serves only documents uploaded straight into AutoRAG.
    """
    headers = {}
    if settings.outline_user_token:
        headers["X-Outline-Token"] = settings.outline_user_token

    try:
        with _client() as client:
            response = client.request(method, path, headers=headers, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(
            f"The AutoRAG API at {settings.api_base_url} could not be reached: {exc}. "
            "Check that it is running and that AUTORAG_API_BASE_URL points at it."
        ) from exc

    if response.status_code == 401:
        raise ApiError(
            "Outline rejected the configured token. Issue a new personal API token "
            "and set AUTORAG_OUTLINE_USER_TOKEN to it."
        )
    if response.status_code == 503:
        raise ApiError(
            "The API could not reach Outline, so what this token may read is unknown "
            "and nothing was searched. This is not an empty wiki."
        )
    if response.status_code >= 400:
        # The token is never quoted back, here or anywhere else in this module.
        raise ApiError(
            f"The AutoRAG API refused the request ({response.status_code}): "
            f"{response.text.strip()}"
        )
    return response.json()


@mcp.tool()
def search_documents(query: str, top_k: int = 5, mode: str | None = None) -> dict:
    """Search the indexed documents.

    `mode` is `hybrid` (the default: meaning and wording together), `vector`
    for meaning alone, or `keyword` for literal wording -- useful when the query
    is an identifier, an error string, or a name that must match exactly.

    Results are limited to what the configured Outline token's owner may read.
    A `warning` in the response is worth repeating to the user: it reports a
    search whose model has nothing indexed under it, which otherwise looks
    exactly like a query with no matches.
    """
    return _request("POST", "/search", json={"query": query, "top_k": top_k, "mode": mode})


@mcp.tool()
def get_chunk_context(chunk_id: str) -> dict:
    """The chunks either side of one search hit, for reading around a result.

    Takes a `chunk_id` from a search result. Returns nothing for a chunk whose
    document is out of scope.
    """
    return _request("GET", f"/chunks/{_identified(chunk_id, 'chunk_id')}/context")


@mcp.tool()
def list_documents() -> dict:
    """Every indexed document the configured token's owner may read."""
    return {"documents": _request("GET", "/documents")}


@mcp.tool()
def get_sync_status(document_id: str) -> dict:
    """What the last indexing run did to one document.

    Reports how many chunks were re-embedded and how many kept their existing
    vector. A `status` of null means the document has not been indexed, or is
    not one this token may read.
    """
    return _request("GET", f"/documents/{_identified(document_id, 'document_id')}/sync-status")


if __name__ == "__main__":
    mcp.run()
