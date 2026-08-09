import inspect

import pytest
from opensql_autorag_api.outline_access import OutlineIdentity
from opensql_autorag_mcp import server as server_module
from opensql_autorag_mcp.server import TOOL_NAMES


def test_mcp_tool_names_are_stable():
    assert TOOL_NAMES == {
        "search_documents",
        "get_chunk_context",
        "list_documents",
        "get_sync_status",
    }


class FakeResolver:
    def __init__(self, collections: tuple[str, ...]) -> None:
        self.collections = collections
        self.tokens: list[str] = []

    def resolve(self, token: str) -> OutlineIdentity:
        self.tokens.append(token)
        return OutlineIdentity(user_id="user-1", user_name="Dana", collection_ids=self.collections)


def test_without_a_configured_token_no_wiki_collection_is_in_scope(monkeypatch):
    """The stdio server has no request to carry a token, so it needs one in its env."""
    fake = FakeResolver(("col-platform",))
    monkeypatch.setattr(server_module, "resolver", fake)
    monkeypatch.setattr(server_module.settings, "outline_user_token", "")

    scope, applied = server_module._scope()

    assert fake.tokens == []
    assert scope.allowed_collection_ids == ()
    assert scope.include_local_documents is True
    assert applied["outline_user"] is None


def test_the_configured_token_decides_the_scope(monkeypatch):
    fake = FakeResolver(("col-platform", "col-eng"))
    monkeypatch.setattr(server_module, "resolver", fake)
    monkeypatch.setattr(server_module.settings, "outline_user_token", "ol_api_token")

    scope, applied = server_module._scope()

    assert fake.tokens == ["ol_api_token"]
    assert scope.allowed_collection_ids == ("col-platform", "col-eng")
    assert applied == {"outline_user": "user-1", "collection_count": 2}


@pytest.mark.parametrize("tool", sorted(TOOL_NAMES))
def test_every_tool_that_returns_content_resolves_a_scope(tool):
    """A scope only search honoured would be a filter with a way around it."""
    source = inspect.getsource(getattr(server_module, tool))

    assert "_scope()" in source, f"{tool} returns data without resolving a caller scope"
