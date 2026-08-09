"""What the MCP server promises: its tools, and how it reaches the API.

The server holds no database connection and no embedding model, so there is no
SQL to test here. What is worth pinning down is that every tool presents the
developer's token to the API, and that a failure arrives as a failure rather
than as an empty result an agent would read as "nothing was found".
"""

import httpx
import pytest
from opensql_autorag_mcp import server as server_module
from opensql_autorag_mcp.server import TOOL_NAMES, ApiError

TOKEN = "ol_api_developer_token"


class FakeApi:
    """Stands in for the AutoRAG API, recording what was asked of it."""

    def __init__(self, status: int = 200, payload=None, text: str = "") -> None:
        self.status = status
        self.payload = payload if payload is not None else {}
        self.text = text
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.text:
            return httpx.Response(self.status, text=self.text)
        return httpx.Response(self.status, json=self.payload)

    @property
    def tokens(self) -> list[str | None]:
        return [request.headers.get("x-outline-token") for request in self.requests]


@pytest.fixture
def api(monkeypatch):
    """Install a fake API and point the server's token at a known value."""

    def install(status: int = 200, payload=None, text: str = "", token: str = TOKEN) -> FakeApi:
        fake = FakeApi(status=status, payload=payload, text=text)
        monkeypatch.setattr(server_module.settings, "outline_user_token", token)
        monkeypatch.setattr(
            server_module,
            "_client",
            lambda: httpx.Client(transport=httpx.MockTransport(fake), base_url="http://api.test"),
        )
        return fake

    return install


def test_mcp_tool_names_are_stable():
    assert TOOL_NAMES == {
        "search_documents",
        "get_chunk_context",
        "list_documents",
        "get_sync_status",
    }


def test_the_server_holds_no_database_of_its_own():
    """The point of asking the API is that this process cannot query around it.

    A developer runs this on their own machine. A database connection here would
    be a database credential on every laptop, and the permission filter lives in
    the SQL this module would then be free to write for itself.
    """
    source = server_module.__file__
    with open(source) as handle:
        text = handle.read()

    for forbidden in ("get_connection", "Repository", "execute_search", "get_embedding_provider"):
        assert forbidden not in text, f"the MCP server reaches past the API via {forbidden}"


def test_search_asks_the_api_and_returns_what_it_said(api):
    fake = api(payload={"query": "pgvector", "results": [{"chunk_id": "c-1"}], "warning": None})

    answer = server_module.search_documents("pgvector", top_k=3, mode="keyword")

    assert answer["results"] == [{"chunk_id": "c-1"}]
    assert fake.requests[0].url.path == "/search"
    assert fake.requests[0].method == "POST"


def test_the_query_reaches_the_api_unchanged(api):
    import json

    fake = api(payload={"results": []})

    server_module.search_documents("ERR_HNSW_2481", top_k=7, mode="keyword")

    sent = json.loads(fake.requests[0].content)
    assert sent == {"query": "ERR_HNSW_2481", "top_k": 7, "mode": "keyword"}


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("search_documents", ("anything",)),
        ("get_chunk_context", ("2b0f6a1e-3c5d-4e7a-9b1f-8c2d4e6a0b31",)),
        ("list_documents", ()),
        ("get_sync_status", ("2b0f6a1e-3c5d-4e7a-9b1f-8c2d4e6a0b31",)),
    ],
)
def test_every_tool_presents_the_callers_token(api, tool, arguments):
    """A tool that asked anonymously would answer from a different scope."""
    fake = api(payload={})

    getattr(server_module, tool)(*arguments)

    assert fake.tokens == [TOKEN]


def test_without_a_token_the_api_is_asked_anonymously(api):
    """Not an error: the API then serves documents uploaded into AutoRAG alone."""
    fake = api(payload={"results": []}, token="")

    server_module.search_documents("anything")

    assert fake.tokens == [None]


def test_a_rejected_token_is_reported_rather_than_returned_empty(api):
    api(status=401, text="Outline rejected the token")

    with pytest.raises(ApiError) as raised:
        server_module.search_documents("anything")

    assert "AUTORAG_OUTLINE_USER_TOKEN" in str(raised.value)


def test_an_unreachable_outline_does_not_read_as_an_empty_wiki(api):
    api(status=503, text="could not reach Outline")

    with pytest.raises(ApiError) as raised:
        server_module.search_documents("anything")

    assert "not an empty wiki" in str(raised.value)


def test_an_unreachable_api_says_so(monkeypatch):
    def refuse() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")

    monkeypatch.setattr(server_module, "_client", refuse)

    with pytest.raises(ApiError) as raised:
        server_module.list_documents()

    assert "could not be reached" in str(raised.value)


def test_the_token_is_never_quoted_back_in_an_error(api):
    """An error crosses into an agent's transcript, and often into a log."""
    api(status=500, text="internal error")

    with pytest.raises(ApiError) as raised:
        server_module.search_documents("anything")

    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize("tool", ["get_chunk_context", "get_sync_status"])
def test_an_id_that_is_not_a_uuid_is_refused_before_it_reaches_a_url(api, tool):
    """Ids arrive as free text from an agent and are spliced into a path."""
    fake = api(payload={})

    with pytest.raises(ValueError):
        getattr(server_module, tool)("../../documents")

    assert fake.requests == []
