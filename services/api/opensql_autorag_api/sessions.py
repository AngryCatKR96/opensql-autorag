"""Server-side sessions holding a signed-in caller's Outline tokens.

Nothing sensitive is given to the browser: the cookie is an opaque random value,
and what the database keeps is its digest, so a dump of `oauth_sessions` cannot be
replayed as a session. The Outline tokens themselves are encrypted with a key
derived from AUTORAG_SESSION_SECRET, because they are credentials to somebody's
company wiki, not to this service.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from psycopg import Connection

from opensql_autorag_api.oauth import REFRESH_MARGIN_SECONDS, OAuthError, TokenSet, oauth
from opensql_autorag_api.settings import settings

logger = logging.getLogger(__name__)

COOKIE_NAME = "autorag_session"


class SessionSecretMissing(Exception):
    """AUTORAG_SESSION_SECRET is required before any token can be stored."""


def _fernet() -> Fernet:
    """A Fernet key from the configured secret.

    The secret is a passphrase rather than a key, so it is hashed to the 32 bytes
    Fernet needs. Changing it invalidates every session, which is the intended way
    to sign everybody out.
    """
    if not settings.session_secret:
        raise SessionSecretMissing("AUTORAG_SESSION_SECRET is not set")
    digest = hashlib.sha256(settings.session_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _digest(cookie_value: str) -> str:
    return hashlib.sha256(cookie_value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Session:
    """A signed-in caller, with a usable Outline access token."""

    cookie_value: str | None
    outline_user_id: str
    outline_user_name: str
    access_token: str


class SessionStore:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    # -- the login round trip ------------------------------------------------

    def remember_login(self, state: str, code_verifier: str, redirect_after: str | None) -> None:
        """Hold the PKCE verifier until Outline sends the caller back."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO oauth_logins (state, code_verifier, redirect_after, expires_at)
                VALUES (%s, %s, %s, now() + make_interval(secs => %s))
                """,
                (state, code_verifier, redirect_after, settings.oauth_login_ttl_seconds),
            )

    def claim_login(self, state: str) -> dict | None:
        """Consume a pending login. Returns None for an unknown or expired state.

        Deleting on read is what stops an authorization code from being replayed
        with the same state twice.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                DELETE FROM oauth_logins
                WHERE state = %s AND expires_at > now()
                RETURNING code_verifier, redirect_after
                """,
                (state,),
            )
            return cursor.fetchone()

    def purge_expired(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM oauth_logins WHERE expires_at <= now()")
            cursor.execute("DELETE FROM oauth_sessions WHERE expires_at <= now()")

    # -- sessions ------------------------------------------------------------

    def create(self, tokens: TokenSet, user_id: str, user_name: str) -> str:
        """Store a new session and return the cookie value to hand to the browser."""
        fernet = _fernet()
        cookie_value = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.session_ttl_seconds)
        access_expires = (
            datetime.fromtimestamp(tokens.expires_at, UTC) if tokens.expires_at else None
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO oauth_sessions (
                    id, outline_user_id, outline_user_name, access_token_encrypted,
                    refresh_token_encrypted, access_token_expires_at, expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    _digest(cookie_value),
                    user_id,
                    user_name,
                    fernet.encrypt(tokens.access_token.encode("utf-8")),
                    (
                        fernet.encrypt(tokens.refresh_token.encode("utf-8"))
                        if tokens.refresh_token
                        else None
                    ),
                    access_expires,
                    expires_at,
                ),
            )
        return cookie_value

    def resolve(self, cookie_value: str) -> Session | None:
        """The session this cookie names, with a live access token.

        An access token at or near expiry is refreshed here, so callers never see
        a token Outline would reject. Outline issues a new refresh token every
        time, so the replacement is written back.
        """
        if not cookie_value:
            return None
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE oauth_sessions
                SET last_used_at = now()
                WHERE id = %s AND expires_at > now()
                RETURNING outline_user_id, outline_user_name, access_token_encrypted,
                          refresh_token_encrypted, access_token_expires_at
                """,
                (_digest(cookie_value),),
            )
            row = cursor.fetchone()
        if row is None:
            return None

        fernet = _fernet()
        try:
            access_token = fernet.decrypt(bytes(row["access_token_encrypted"])).decode("utf-8")
            refresh_token = (
                fernet.decrypt(bytes(row["refresh_token_encrypted"])).decode("utf-8")
                if row["refresh_token_encrypted"]
                else None
            )
        except InvalidToken:
            # AUTORAG_SESSION_SECRET changed, so this session cannot be read.
            logger.info("dropping a session that no longer decrypts")
            self.delete(cookie_value)
            return None

        if self._needs_refresh(row["access_token_expires_at"]):
            if not refresh_token:
                self.delete(cookie_value)
                return None
            try:
                renewed = oauth.refresh(refresh_token)
            except OAuthError as exc:
                logger.info("could not refresh a session, signing it out: %s", exc)
                self.delete(cookie_value)
                return None
            self._store_tokens(cookie_value, renewed, fernet)
            access_token = renewed.access_token

        return Session(
            cookie_value=cookie_value,
            outline_user_id=str(row["outline_user_id"]),
            outline_user_name=str(row["outline_user_name"]),
            access_token=access_token,
        )

    @staticmethod
    def _needs_refresh(expires_at: datetime | None) -> bool:
        if expires_at is None:
            return False
        return expires_at <= datetime.now(UTC) + timedelta(seconds=REFRESH_MARGIN_SECONDS)

    def _store_tokens(self, cookie_value: str, tokens: TokenSet, fernet: Fernet) -> None:
        access_expires = (
            datetime.fromtimestamp(tokens.expires_at, UTC) if tokens.expires_at else None
        )
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE oauth_sessions
                SET access_token_encrypted = %s,
                    refresh_token_encrypted = COALESCE(%s, refresh_token_encrypted),
                    access_token_expires_at = %s
                WHERE id = %s
                """,
                (
                    fernet.encrypt(tokens.access_token.encode("utf-8")),
                    (
                        fernet.encrypt(tokens.refresh_token.encode("utf-8"))
                        if tokens.refresh_token
                        else None
                    ),
                    access_expires,
                    _digest(cookie_value),
                ),
            )

    def delete(self, cookie_value: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("DELETE FROM oauth_sessions WHERE id = %s", (_digest(cookie_value),))
