"""The OAuth code flow against a stand-in Outline.

The properties worth pinning down are the ones that make the flow safe rather
than merely working: the PKCE verifier never leaves this service, every login gets
its own state, and the endpoints come from Outline's discovery document instead of
being assumed.
"""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from opensql_autorag_api import oauth as oauth_module
from opensql_autorag_api.oauth import OAuthError, OAuthNotConfigured, OutlineOAuth

ORIGIN = "https://wiki.example.com"


class FakeOutline:
    """Answers discovery and token requests the way Outline does."""

    def __init__(self, discovery: dict | None = None) -> None:
        self.discovery = (
            discovery
            if discovery is not None
            else {
                "issuer": ORIGIN,
                "authorization_endpoint": f"{ORIGIN}/oauth/authorize",
                "token_endpoint": f"{ORIGIN}/oauth/token",
                "revocation_endpoint": f"{ORIGIN}/oauth/revoke",
                "code_challenge_methods_supported": ["S256"],
            }
        )
        self.token_requests: list[dict] = []
        self.revocations: list[dict] = []
        self.discovery_calls = 0
        self.token_status = 200
        self.token_body: dict | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            self.discovery_calls += 1
            return httpx.Response(200, json=self.discovery)
        if path == "/oauth/token":
            form = {
                key: value[0]
                for key, value in parse_qs(request.content.decode("utf-8")).items()
            }
            self.token_requests.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, text="invalid_grant")
            return httpx.Response(
                200,
                json=self.token_body
                or {
                    "access_token": "oat_new",
                    "refresh_token": "ort_new",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": "read",
                },
            )
        if path == "/oauth/revoke":
            form = {
                key: value[0]
                for key, value in parse_qs(request.content.decode("utf-8")).items()
            }
            self.revocations.append(form)
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404, json={"error": "not_found"})


@pytest.fixture
def outline(monkeypatch):
    fake = FakeOutline()
    monkeypatch.setattr(oauth_module.settings, "outline_base_url", ORIGIN)
    monkeypatch.setattr(oauth_module.settings, "outline_oauth_client_id", "client-1")
    monkeypatch.setattr(oauth_module.settings, "outline_oauth_client_secret", "secret-1")
    monkeypatch.setattr(oauth_module.settings, "session_secret", "session-secret")
    monkeypatch.setattr(oauth_module.settings, "public_base_url", "https://autorag.example.com")
    monkeypatch.setattr(oauth_module.settings, "oauth_discovery_cache_seconds", 600)
    fake.client = OutlineOAuth(transport=httpx.MockTransport(fake.handler))
    return fake


def query_of(url: str) -> dict[str, str]:
    return {key: value[0] for key, value in parse_qs(urlparse(url).query).items()}


def test_endpoints_come_from_the_discovery_document(outline):
    endpoints = outline.client.endpoints()

    assert endpoints.authorization_endpoint == f"{ORIGIN}/oauth/authorize"
    assert endpoints.token_endpoint == f"{ORIGIN}/oauth/token"
    assert endpoints.revocation_endpoint == f"{ORIGIN}/oauth/revoke"


def test_discovery_is_fetched_once(outline):
    outline.client.endpoints()
    outline.client.endpoints()

    assert outline.discovery_calls == 1


def test_a_document_without_a_token_endpoint_points_at_the_base_url(outline):
    outline.discovery = {"authorization_endpoint": f"{ORIGIN}/oauth/authorize"}
    outline.client.invalidate()

    with pytest.raises(OAuthError) as raised:
        outline.client.endpoints()

    assert "AUTORAG_OUTLINE_BASE_URL" in str(raised.value)


def test_an_unreachable_instance_is_an_oauth_error(monkeypatch):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(oauth_module.settings, "outline_base_url", ORIGIN)
    client = OutlineOAuth(transport=httpx.MockTransport(refuse))

    with pytest.raises(OAuthError):
        client.endpoints()


def test_the_authorization_url_carries_state_and_an_s256_challenge(outline):
    pending = outline.client.begin()

    query = query_of(pending.authorization_url)
    assert query["state"] == pending.state
    assert query["code_challenge_method"] == "S256"
    assert query["response_type"] == "code"
    assert query["client_id"] == "client-1"
    assert query["scope"] == "read"


def test_the_pkce_verifier_never_appears_in_the_url(outline):
    """It is the one value that must stay on this side of the round trip."""
    pending = outline.client.begin()

    assert pending.code_verifier not in pending.authorization_url


def test_the_challenge_is_the_s256_hash_of_the_verifier(outline):
    pending = outline.client.begin()

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(pending.code_verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    assert query_of(pending.authorization_url)["code_challenge"] == expected


def test_every_login_gets_its_own_state_and_verifier(outline):
    first = outline.client.begin()
    second = outline.client.begin()

    assert first.state != second.state
    assert first.code_verifier != second.code_verifier


def test_the_redirect_uri_is_built_from_the_public_base_url(outline):
    pending = outline.client.begin()

    assert (
        query_of(pending.authorization_url)["redirect_uri"]
        == "https://autorag.example.com/auth/outline/callback"
    )


def test_exchanging_a_code_sends_the_verifier_and_the_client_secret(outline):
    tokens = outline.client.exchange("the-code", "the-verifier")

    sent = outline.token_requests[-1]
    assert sent["grant_type"] == "authorization_code"
    assert sent["code"] == "the-code"
    assert sent["code_verifier"] == "the-verifier"
    assert sent["client_secret"] == "secret-1"
    assert tokens.access_token == "oat_new"
    assert tokens.refresh_token == "ort_new"
    assert tokens.expires_in == 3600


def test_a_refused_exchange_is_an_oauth_error(outline):
    outline.token_status = 400

    with pytest.raises(OAuthError) as raised:
        outline.client.exchange("stale-code", "the-verifier")

    assert "authorization_code" in str(raised.value)


def test_a_token_response_without_an_access_token_is_rejected(outline):
    outline.token_body = {"token_type": "Bearer"}

    with pytest.raises(OAuthError):
        outline.client.exchange("the-code", "the-verifier")


def test_refreshing_sends_the_refresh_grant(outline):
    tokens = outline.client.refresh("ort_old")

    sent = outline.token_requests[-1]
    assert sent["grant_type"] == "refresh_token"
    assert sent["refresh_token"] == "ort_old"
    # Outline always issues a new refresh token and revokes the one just used.
    assert tokens.refresh_token == "ort_new"


def test_revocation_reaches_outline(outline):
    outline.client.revoke("oat_new")

    assert outline.revocations[-1]["token"] == "oat_new"


def test_revocation_failing_does_not_raise(monkeypatch):
    """Signing out locally has to succeed even when Outline cannot be reached."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(oauth_module.settings, "outline_base_url", ORIGIN)
    client = OutlineOAuth(transport=httpx.MockTransport(refuse))

    client.revoke("oat_new")


@pytest.mark.parametrize(
    "missing",
    ["outline_oauth_client_id", "outline_oauth_client_secret", "session_secret"],
)
def test_an_incomplete_configuration_refuses_to_start_a_login(outline, monkeypatch, missing):
    monkeypatch.setattr(oauth_module.settings, missing, "")

    assert outline.client.configured is False
    with pytest.raises(OAuthNotConfigured):
        outline.client.begin()


def test_the_discovery_document_is_read_over_http_not_guessed(outline):
    """A self-hosted instance behind a proxy reports its own reachable origin."""
    outline.discovery = {
        **outline.discovery,
        "authorization_endpoint": "https://wiki.internal:8443/oauth/authorize",
        "token_endpoint": "https://wiki.internal:8443/oauth/token",
    }
    outline.client.invalidate()

    pending = outline.client.begin()
    outline.client.exchange("the-code", pending.code_verifier)

    assert pending.authorization_url.startswith("https://wiki.internal:8443/oauth/authorize?")
    assert outline.client.endpoints().token_endpoint == "https://wiki.internal:8443/oauth/token"
