"""The /auth/outline endpoints, over HTTP against the real database.

The callback is the part an attacker can reach with values of their own choosing,
so most of these are about what it refuses.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from opensql_autorag_api import main as main_module
from opensql_autorag_api import sessions as sessions_module
from opensql_autorag_api.oauth import OAuthError, OAuthNotConfigured, PendingLogin, TokenSet
from opensql_autorag_api.outline_access import OutlineIdentity
from opensql_autorag_api.sessions import COOKIE_NAME


class FakeOAuth:
    """Stands in for Outline's OAuth endpoints."""

    def __init__(self) -> None:
        self.configured = True
        self.begin_error: Exception | None = None
        self.exchange_error: Exception | None = None
        self.revoked: list[str] = []
        self.exchanged: list[tuple[str, str]] = []

    def begin(self, redirect_after=None) -> PendingLogin:
        if self.begin_error:
            raise self.begin_error
        return PendingLogin(
            state="the-state",
            code_verifier="the-verifier",
            authorization_url="https://wiki.example.com/oauth/authorize?state=the-state",
        )

    def exchange(self, code: str, code_verifier: str) -> TokenSet:
        self.exchanged.append((code, code_verifier))
        if self.exchange_error:
            raise self.exchange_error
        return TokenSet(access_token="oat_new", refresh_token="ort_new", expires_in=3600)

    def revoke(self, token: str) -> None:
        self.revoked.append(token)


class FakeResolver:
    """Resolves each token to its own identity, so precedence is observable."""

    def __init__(self) -> None:
        self.error: Exception | None = None
        self.identities = {
            "oat_new": OutlineIdentity("user-1", "Dana", ("col-platform",)),
            "another-users-token": OutlineIdentity("user-2", "Sam", ("col-a", "col-b")),
        }

    def resolve(self, token: str) -> OutlineIdentity:
        if self.error:
            raise self.error
        return self.identities.get(token, OutlineIdentity("user-unknown", "", ()))


@pytest.fixture
def client(db_connection, monkeypatch):
    """The API wired to the rolled-back test connection and a fake Outline."""

    @contextmanager
    def shared_connection():
        yield db_connection

    monkeypatch.setattr(main_module, "get_connection", shared_connection)
    monkeypatch.setattr(sessions_module.settings, "session_secret", "test-session-secret")
    monkeypatch.setattr(main_module.settings, "session_ttl_seconds", 3600)

    fake_oauth = FakeOAuth()
    fake_resolver = FakeResolver()
    monkeypatch.setattr(main_module, "oauth", fake_oauth)
    monkeypatch.setattr(main_module, "resolver", fake_resolver)

    test_client = TestClient(main_module.app, follow_redirects=False)
    test_client.oauth = fake_oauth
    test_client.resolver = fake_resolver
    return test_client


def complete_login(client: TestClient) -> str:
    """Run a login the way a browser would, returning the session cookie."""
    client.get("/auth/outline/login")
    response = client.get("/auth/outline/callback?code=the-code&state=the-state")
    assert response.status_code == 303
    return response.cookies[COOKIE_NAME]


# -- starting a login --------------------------------------------------------


def test_login_redirects_to_outline(client):
    response = client.get("/auth/outline/login")

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://wiki.example.com/oauth/authorize")


def test_login_without_configuration_says_it_is_not_available(client):
    client.oauth.begin_error = OAuthNotConfigured("no client id")

    response = client.get("/auth/outline/login")

    assert response.status_code == 501


def test_login_when_outline_is_down_is_a_503(client):
    client.oauth.begin_error = OAuthError("cannot reach Outline")

    assert client.get("/auth/outline/login").status_code == 503


# -- the callback ------------------------------------------------------------


def test_a_completed_login_sets_an_http_only_session_cookie(client):
    client.get("/auth/outline/login")

    response = client.get("/auth/outline/callback?code=the-code&state=the-state")

    assert response.status_code == 303
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "samesite=lax" in header.lower()
    # The PKCE verifier held server side is what gets sent, not anything from the URL.
    assert client.oauth.exchanged == [("the-code", "the-verifier")]


def test_a_state_this_service_never_issued_is_refused(client):
    response = client.get("/auth/outline/callback?code=stolen&state=attacker-chosen")

    assert response.status_code == 400
    assert COOKIE_NAME not in response.cookies


def test_a_callback_cannot_be_replayed(client):
    client.get("/auth/outline/login")
    first = client.get("/auth/outline/callback?code=the-code&state=the-state")
    client.cookies.clear()
    second = client.get("/auth/outline/callback?code=the-code&state=the-state")

    assert first.status_code == 303
    assert second.status_code == 400


def test_a_callback_without_a_state_is_refused(client):
    assert client.get("/auth/outline/callback?code=the-code").status_code == 400


def test_a_callback_without_a_code_is_refused(client):
    assert client.get("/auth/outline/callback?state=the-state").status_code == 400


def test_an_authorization_the_user_declined_is_reported(client):
    response = client.get("/auth/outline/callback?error=access_denied")

    assert response.status_code == 400
    assert "access_denied" in response.json()["detail"]


def test_a_refused_code_exchange_does_not_create_a_session(client):
    client.get("/auth/outline/login")
    client.oauth.exchange_error = OAuthError("invalid_grant")

    response = client.get("/auth/outline/callback?code=stale&state=the-state")

    assert response.status_code == 502
    assert COOKIE_NAME not in response.cookies


def test_the_redirect_target_cannot_be_pointed_at_another_host(client):
    """Otherwise the login endpoint is an open redirect."""
    client.get("/auth/outline/login?next=https://evil.example.com/steal")
    response = client.get("/auth/outline/callback?code=the-code&state=the-state")

    assert response.headers["location"] == "/"


def test_a_relative_redirect_target_is_kept(client):
    client.get("/auth/outline/login?next=/documents")
    response = client.get("/auth/outline/callback?code=the-code&state=the-state")

    assert response.headers["location"] == "/documents"


def test_a_protocol_relative_redirect_target_is_rejected(client):
    client.get("/auth/outline/login?next=//evil.example.com")
    response = client.get("/auth/outline/callback?code=the-code&state=the-state")

    assert response.headers["location"] == "/"


# -- using and ending a session ---------------------------------------------


def test_the_session_identifies_the_caller_for_search_scope(client):
    complete_login(client)

    body = client.post("/search", json={"query": "pgvector", "top_k": 3}).json()

    assert body["scope"] == {"outline_user": "user-1", "collection_count": 1}


def test_me_reports_who_is_signed_in(client):
    complete_login(client)

    body = client.get("/auth/outline/me").json()

    assert body["outline_user"] == "user-1"
    assert body["outline_user_name"] == "Dana"
    assert body["login_available"] is True


def test_me_without_a_session_reports_nobody(client):
    body = client.get("/auth/outline/me").json()

    assert body["outline_user"] is None


def test_signing_out_drops_the_session_and_revokes_the_token(client):
    complete_login(client)

    client.post("/auth/outline/logout")

    assert client.get("/auth/outline/me").json()["outline_user"] is None
    assert client.oauth.revoked == ["oat_new"]


def test_signing_out_without_a_session_is_not_an_error(client):
    assert client.post("/auth/outline/logout").status_code == 200


def test_a_header_token_wins_over_the_session_cookie(client):
    """A machine caller's explicit token is a deliberate act; the cookie is ambient."""
    complete_login(client)

    with_cookie_only = client.post("/search", json={"query": "pgvector", "top_k": 3}).json()
    with_header = client.post(
        "/search",
        json={"query": "pgvector", "top_k": 3},
        headers={"X-Outline-Token": "another-users-token"},
    ).json()

    assert with_cookie_only["scope"]["outline_user"] == "user-1"
    assert with_header["scope"] == {"outline_user": "user-2", "collection_count": 2}


def test_a_forged_session_cookie_is_ignored(client):
    client.cookies.set(COOKIE_NAME, "made-up-session-value")

    body = client.post("/search", json={"query": "pgvector", "top_k": 3}).json()

    assert body["scope"] == {"outline_user": None, "collection_count": 0}
