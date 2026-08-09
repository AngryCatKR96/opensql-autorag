"""The Outline half of signing a caller in with OAuth.

Outline implements the authorization code flow with PKCE and publishes its
endpoints at `/.well-known/oauth-authorization-server`
(`server/routes/index.ts`), so the endpoint paths are discovered rather than
hardcoded — a self-hosted instance behind a reverse proxy reports the origin it
is actually reachable at.

What comes out of this module is an Outline access token belonging to the person
who signed in. Everything downstream treats it exactly like a personal API token,
so the permission filter does not change: see `outline_access.py`.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from opensql_autorag_api.settings import settings

logger = logging.getLogger(__name__)

# Outline advertises `read` and `write`. Nothing here writes to the wiki.
READ_SCOPE = "read"

# Refresh an access token this long before it actually expires, so a request does
# not race the expiry.
REFRESH_MARGIN_SECONDS = 60


class OAuthNotConfigured(Exception):
    """Signing in with Outline needs a client id, secret, and session secret."""


class OAuthError(Exception):
    """Outline refused a step of the flow."""


@dataclass(frozen=True)
class Endpoints:
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_in: int | None

    @property
    def expires_at(self) -> float | None:
        return time.time() + self.expires_in if self.expires_in else None


@dataclass(frozen=True)
class PendingLogin:
    """What has to survive the round trip to Outline, held server side."""

    state: str
    code_verifier: str
    authorization_url: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


class OutlineOAuth:
    """Discovers Outline's OAuth endpoints and runs the code flow against them."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._endpoints: tuple[float, Endpoints] | None = None
        self._lock = threading.Lock()
        # Only set in tests.
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(
            settings.outline_oauth_client_id
            and settings.outline_oauth_client_secret
            and settings.session_secret
        )

    def _require_configured(self) -> None:
        if not self.configured:
            raise OAuthNotConfigured(
                "set AUTORAG_OUTLINE_OAUTH_CLIENT_ID, "
                "AUTORAG_OUTLINE_OAUTH_CLIENT_SECRET and AUTORAG_SESSION_SECRET"
            )

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=settings.outline_base_url.rstrip("/"),
            timeout=settings.outline_timeout_seconds,
            transport=self._transport,
        )

    def invalidate(self) -> None:
        with self._lock:
            self._endpoints = None

    def endpoints(self) -> Endpoints:
        """Outline's OAuth endpoints, from its discovery document."""
        with self._lock:
            cached = self._endpoints
            if cached is not None and cached[0] > time.monotonic():
                return cached[1]

        try:
            with self._client() as client:
                response = client.get("/.well-known/oauth-authorization-server")
                response.raise_for_status()
                document = response.json()
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not read Outline's OAuth discovery document: {exc}") from exc

        try:
            resolved = Endpoints(
                authorization_endpoint=str(document["authorization_endpoint"]),
                token_endpoint=str(document["token_endpoint"]),
                revocation_endpoint=(
                    str(document["revocation_endpoint"])
                    if document.get("revocation_endpoint")
                    else None
                ),
            )
        except KeyError as exc:
            raise OAuthError(
                f"Outline's discovery document is missing {exc}; "
                "check AUTORAG_OUTLINE_BASE_URL points at the Outline root"
            ) from exc

        with self._lock:
            self._endpoints = (time.monotonic() + settings.oauth_discovery_cache_seconds, resolved)
        return resolved

    def begin(self, redirect_after: str | None = None) -> PendingLogin:
        """Build the URL that sends a caller to Outline to authorize us.

        `state` is required by Outline (`allowEmptyState: false`) and is what ties
        the callback to this request. The PKCE verifier never leaves this service;
        only its S256 challenge is sent.
        """
        self._require_configured()
        state = _b64url(secrets.token_bytes(32))
        code_verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())

        query = urlencode(
            {
                "client_id": settings.outline_oauth_client_id,
                "redirect_uri": settings.oauth_redirect_uri,
                "response_type": "code",
                "scope": settings.outline_oauth_scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        endpoint = self.endpoints().authorization_endpoint
        return PendingLogin(
            state=state,
            code_verifier=code_verifier,
            authorization_url=f"{endpoint}?{query}",
        )

    def exchange(self, code: str, code_verifier: str) -> TokenSet:
        """Trade an authorization code for the caller's tokens."""
        self._require_configured()
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oauth_redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        """Renew an expired access token.

        Outline is configured with `alwaysIssueNewRefreshToken`, so the refresh
        token that comes back replaces the one used here and the old one stops
        working. The caller must persist it.
        """
        self._require_configured()
        return self._token_request(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    def _token_request(self, form: dict[str, str]) -> TokenSet:
        # Outline supports client_secret_post, so the credentials go in the body
        # rather than in a Basic auth header.
        payload = {
            **form,
            "client_id": settings.outline_oauth_client_id,
            "client_secret": settings.outline_oauth_client_secret,
        }
        endpoint = self.endpoints().token_endpoint
        try:
            with self._client() as client:
                response = client.post(endpoint, data=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            detail = ""
            if exc.response is not None:
                detail = exc.response.text[:300]
            raise OAuthError(
                f"Outline rejected the {form['grant_type']} request: {detail or exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise OAuthError(f"could not reach Outline's token endpoint: {exc}") from exc

        access_token = body.get("access_token")
        if not access_token:
            raise OAuthError("Outline's token response carried no access_token")
        expires_in = body.get("expires_in")
        return TokenSet(
            access_token=str(access_token),
            refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
            expires_in=int(expires_in) if expires_in else None,
        )

    def revoke(self, token: str) -> None:
        """Best effort revocation, so signing out invalidates the token at Outline too."""
        try:
            endpoint = self.endpoints().revocation_endpoint
        except OAuthError:
            return
        if not endpoint:
            return
        try:
            with self._client() as client:
                client.post(
                    endpoint,
                    data={
                        "token": token,
                        "client_id": settings.outline_oauth_client_id,
                        "client_secret": settings.outline_oauth_client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            # Signing out locally still has to succeed.
            logger.warning("could not revoke the Outline token: %s", exc)


oauth = OutlineOAuth()
