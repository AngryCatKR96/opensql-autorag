"""How a search request is turned into a caller identity and a scope.

These cover the HTTP boundary only: which header is read, and what happens when
Outline refuses or cannot be reached. Whether the resulting scope actually filters
rows is covered in test_search_scope.py against the database.
"""

import pytest
from fastapi import HTTPException
from opensql_autorag_api import main as main_module
from opensql_autorag_api.outline_access import (
    InvalidOutlineToken,
    OutlineIdentity,
    OutlineUnavailable,
)
from starlette.requests import Request

TOKEN = "ol_api_caller_token"


def request_with(**headers: str) -> Request:
    raw = [(key.replace("_", "-").encode(), value.encode()) for key, value in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/search", "headers": raw})


class FakeResolver:
    def __init__(self, identity=None, error=None) -> None:
        self.identity = identity
        self.error = error
        self.tokens: list[str] = []

    def resolve(self, token: str) -> OutlineIdentity:
        self.tokens.append(token)
        if self.error is not None:
            raise self.error
        return self.identity


@pytest.fixture
def resolver(monkeypatch):
    def install(identity=None, error=None) -> FakeResolver:
        fake = FakeResolver(identity=identity, error=error)
        monkeypatch.setattr(main_module, "resolver", fake)
        return fake

    return install


def identity(*collections: str) -> OutlineIdentity:
    return OutlineIdentity(user_id="user-1", user_name="Dana", collection_ids=collections)


def test_the_explicit_header_carries_the_token(resolver):
    fake = resolver(identity=identity("col-platform"))

    scope, applied = main_module._resolve_scope(request_with(x_outline_token=TOKEN))

    assert fake.tokens == [TOKEN]
    assert scope.allowed_collection_ids == ("col-platform",)
    assert applied["outline_user"] == "user-1"
    assert applied["collection_count"] == 1


def test_a_bearer_token_is_accepted_too(resolver):
    fake = resolver(identity=identity("col-platform"))

    main_module._resolve_scope(request_with(authorization=f"Bearer {TOKEN}"))

    assert fake.tokens == [TOKEN]


def test_the_explicit_header_wins_over_authorization(resolver):
    fake = resolver(identity=identity())

    main_module._resolve_scope(
        request_with(x_outline_token=TOKEN, authorization="Bearer other-token")
    )

    assert fake.tokens == [TOKEN]


def test_a_non_bearer_authorization_is_not_read_as_a_token(resolver):
    fake = resolver(identity=identity())

    scope, applied = main_module._resolve_scope(request_with(authorization="Basic dXNlcjpwdw=="))

    assert fake.tokens == []
    assert applied["outline_user"] is None
    assert scope.allowed_collection_ids == ()


def test_a_caller_without_a_token_reaches_no_wiki_collection(resolver):
    fake = resolver(identity=identity("col-platform"))

    scope, applied = main_module._resolve_scope(request_with())

    assert fake.tokens == []
    assert scope.allowed_collection_ids == ()
    assert scope.include_local_documents is True
    assert applied == {"outline_user": None, "collection_count": 0}


def test_a_rejected_token_is_a_401(resolver):
    resolver(error=InvalidOutlineToken("Outline rejected the token"))

    with pytest.raises(HTTPException) as raised:
        main_module._resolve_scope(request_with(x_outline_token="stale-token"))

    assert raised.value.status_code == 401


def test_an_unreachable_outline_is_a_503_rather_than_an_empty_wiki(resolver):
    """Serving zero wiki results would read as "the wiki has nothing on this"."""
    resolver(error=OutlineUnavailable("could not reach Outline"))

    with pytest.raises(HTTPException) as raised:
        main_module._resolve_scope(request_with(x_outline_token=TOKEN))

    assert raised.value.status_code == 503


def test_a_caller_whose_token_reads_nothing_gets_no_wiki_results(resolver):
    """An Outline user with no collections is not the same as an error."""
    resolver(identity=identity())

    scope, applied = main_module._resolve_scope(request_with(x_outline_token=TOKEN))

    assert scope.allowed_collection_ids == ()
    assert applied["outline_user"] == "user-1"


def test_whitespace_around_a_token_is_ignored(resolver):
    fake = resolver(identity=identity())

    main_module._resolve_scope(request_with(x_outline_token=f"  {TOKEN}  "))

    assert fake.tokens == [TOKEN]
