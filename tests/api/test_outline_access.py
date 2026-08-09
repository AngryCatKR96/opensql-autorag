import json

import httpx
import pytest
from opensql_autorag_api import outline_access
from opensql_autorag_api.outline_access import (
    InvalidOutlineToken,
    OutlineAccessResolver,
    OutlineUnavailable,
)

TOKEN = "ol_api_caller_token"


class FakeOutline:
    """A stand-in Outline that only answers for one token."""

    def __init__(self, collections: list[str], token: str = TOKEN, page_size: int = 50) -> None:
        self.collections = collections
        self.token = token
        self.page_size = page_size
        self.requests: list[tuple[str, dict]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content or b"{}")
        endpoint = request.url.path.removeprefix("/api/")
        self.requests.append((endpoint, payload))

        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "authentication_required"})

        if endpoint == "auth.info":
            return httpx.Response(
                200, json={"data": {"user": {"id": "user-1", "name": "Dana"}}}
            )
        if endpoint == "collections.list":
            offset = payload.get("offset", 0)
            limit = payload.get("limit", self.page_size)
            page = self.collections[offset : offset + limit]
            return httpx.Response(200, json={"data": [{"id": cid} for cid in page]})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def outline(monkeypatch):
    """A fake Outline, plus a resolver wired to it."""
    fake = FakeOutline(collections=["col-platform", "col-eng"])
    monkeypatch.setattr(outline_access.settings, "outline_base_url", "https://wiki.example.com")
    monkeypatch.setattr(outline_access.settings, "access_cache_seconds", 60)
    fake.resolver = OutlineAccessResolver(transport=httpx.MockTransport(fake.handler))
    return fake


def test_a_token_resolves_to_the_collections_its_owner_can_read(outline):
    identity = outline.resolver.resolve(TOKEN)

    assert identity.user_id == "user-1"
    assert identity.collection_ids == ("col-platform", "col-eng")


def test_the_resolved_scope_allows_those_collections_and_local_documents(outline):
    scope = outline.resolver.resolve(TOKEN).scope()

    assert scope.allowed_collection_ids == ("col-platform", "col-eng")
    assert scope.include_local_documents is True


def test_collections_are_paged_through(outline, monkeypatch):
    outline.collections = [f"col-{index}" for index in range(7)]
    monkeypatch.setattr(outline_access.settings, "outline_page_size", 3)

    identity = outline.resolver.resolve(TOKEN)

    assert len(identity.collection_ids) == 7
    offsets = [body.get("offset") for name, body in outline.requests if name == "collections.list"]
    assert offsets == [0, 3, 6]


def test_the_admin_widening_parameters_are_never_sent(outline):
    """Outline skips its membership filter for an admin who passes either of these."""
    outline.resolver.resolve(TOKEN)

    for name, body in outline.requests:
        if name == "collections.list":
            assert "includeListOnly" not in body
            assert "statusFilter" not in body


def test_a_rejected_token_is_not_treated_as_zero_collections(outline):
    with pytest.raises(InvalidOutlineToken):
        outline.resolver.resolve("not-the-token")


def test_an_empty_token_is_rejected_without_calling_outline(outline):
    with pytest.raises(InvalidOutlineToken):
        outline.resolver.resolve("")

    assert outline.requests == []


def test_an_unreachable_outline_is_distinguished_from_a_bad_token():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    resolver = OutlineAccessResolver(transport=httpx.MockTransport(refuse))

    with pytest.raises(OutlineUnavailable):
        resolver.resolve(TOKEN)


def test_a_resolved_scope_is_reused_instead_of_asking_outline_again(outline):
    outline.resolver.resolve(TOKEN)
    calls_after_first = len(outline.requests)
    outline.resolver.resolve(TOKEN)

    assert len(outline.requests) == calls_after_first


def test_the_cache_expires(outline, monkeypatch):
    monkeypatch.setattr(outline_access.settings, "access_cache_seconds", 0)
    outline.resolver.resolve(TOKEN)
    calls_after_first = len(outline.requests)
    outline.resolver.resolve(TOKEN)

    assert len(outline.requests) > calls_after_first


def test_two_callers_do_not_share_a_scope(outline):
    """The cache is keyed per token, so one caller's access is not another's."""
    outline.resolver.resolve(TOKEN)

    outline.token = "second-token"
    with pytest.raises(InvalidOutlineToken):
        outline.resolver.resolve("second-token-wrong")
    identity = outline.resolver.resolve("second-token")

    assert identity.user_id == "user-1"
