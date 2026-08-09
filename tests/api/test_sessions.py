"""Sessions holding somebody's Outline tokens, against the real database.

What matters here is what a database dump would give an attacker, and that a
session cannot outlive the access it was granted.
"""

import hashlib

import pytest
from opensql_autorag_api import sessions as sessions_module
from opensql_autorag_api.oauth import OAuthError, TokenSet
from opensql_autorag_api.sessions import SessionStore

SECRET = "test-session-secret"


@pytest.fixture
def store(db_connection, monkeypatch):
    monkeypatch.setattr(sessions_module.settings, "session_secret", SECRET)
    monkeypatch.setattr(sessions_module.settings, "session_ttl_seconds", 3600)
    monkeypatch.setattr(sessions_module.settings, "oauth_login_ttl_seconds", 600)
    return SessionStore(db_connection)


def tokens(access="oat_first", refresh="ort_first", expires_in=3600) -> TokenSet:
    return TokenSet(access_token=access, refresh_token=refresh, expires_in=expires_in)


def row_for(store: SessionStore, cookie: str) -> dict:
    with store.connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM oauth_sessions WHERE id = %s",
            (hashlib.sha256(cookie.encode()).hexdigest(),),
        )
        return cursor.fetchone()


# -- the login round trip ----------------------------------------------------


def test_a_pending_login_is_consumed_on_use(store):
    store.remember_login("state-1", "verifier-1", "/")

    first = store.claim_login("state-1")
    second = store.claim_login("state-1")

    assert first["code_verifier"] == "verifier-1"
    assert second is None


def test_an_unknown_state_claims_nothing(store):
    assert store.claim_login("never-issued") is None


def test_an_expired_login_cannot_be_claimed(store, monkeypatch):
    monkeypatch.setattr(sessions_module.settings, "oauth_login_ttl_seconds", -1)
    store.remember_login("state-old", "verifier", None)

    assert store.claim_login("state-old") is None


def test_purging_removes_expired_logins(store, monkeypatch):
    monkeypatch.setattr(sessions_module.settings, "oauth_login_ttl_seconds", -1)
    store.remember_login("state-old", "verifier", None)

    store.purge_expired()

    with store.connection.cursor() as cursor:
        cursor.execute("SELECT count(*) AS left FROM oauth_logins WHERE state = 'state-old'")
        assert cursor.fetchone()["left"] == 0


# -- what the database holds -------------------------------------------------


def test_a_session_round_trips(store):
    cookie = store.create(tokens(), "user-1", "Dana")

    session = store.resolve(cookie)

    assert session is not None
    assert session.outline_user_id == "user-1"
    assert session.outline_user_name == "Dana"
    assert session.access_token == "oat_first"


def test_the_cookie_value_is_not_what_is_stored(store):
    """A dump of the table cannot be replayed as a session cookie."""
    cookie = store.create(tokens(), "user-1", "Dana")

    with store.connection.cursor() as cursor:
        cursor.execute("SELECT id FROM oauth_sessions")
        stored = [r["id"] for r in cursor.fetchall()]

    assert cookie not in stored


def test_the_outline_tokens_are_not_stored_as_plain_text(store):
    cookie = store.create(tokens(access="oat_secret", refresh="ort_secret"), "user-1", "Dana")

    row = row_for(store, cookie)

    assert b"oat_secret" not in bytes(row["access_token_encrypted"])
    assert b"ort_secret" not in bytes(row["refresh_token_encrypted"])


def test_an_unknown_cookie_resolves_to_nothing(store):
    assert store.resolve("not-a-session") is None


def test_an_empty_cookie_resolves_to_nothing(store):
    assert store.resolve("") is None


def test_an_expired_session_resolves_to_nothing(store, monkeypatch):
    monkeypatch.setattr(sessions_module.settings, "session_ttl_seconds", -1)
    cookie = store.create(tokens(), "user-1", "Dana")

    assert store.resolve(cookie) is None


def test_deleting_a_session_signs_it_out(store):
    cookie = store.create(tokens(), "user-1", "Dana")

    store.delete(cookie)

    assert store.resolve(cookie) is None


def test_rotating_the_session_secret_invalidates_existing_sessions(store, monkeypatch):
    """The intended way to sign everybody out."""
    cookie = store.create(tokens(), "user-1", "Dana")
    monkeypatch.setattr(sessions_module.settings, "session_secret", "a-different-secret")

    assert store.resolve(cookie) is None
    # The unreadable row is dropped rather than left to fail forever.
    assert row_for(store, cookie) is None


# -- refresh -----------------------------------------------------------------


class FakeOAuth:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    def refresh(self, refresh_token: str) -> TokenSet:
        self.calls.append(refresh_token)
        if self.error:
            raise self.error
        return TokenSet(access_token="oat_renewed", refresh_token="ort_renewed", expires_in=3600)


def test_a_token_near_expiry_is_refreshed_before_it_is_handed_out(store, monkeypatch):
    fake = FakeOAuth()
    monkeypatch.setattr(sessions_module, "oauth", fake)
    cookie = store.create(tokens(expires_in=10), "user-1", "Dana")

    session = store.resolve(cookie)

    assert fake.calls == ["ort_first"]
    assert session.access_token == "oat_renewed"


def test_the_rotated_refresh_token_is_persisted(store, monkeypatch):
    """Outline revokes the refresh token it just consumed, so the new one must stick."""
    fake = FakeOAuth()
    monkeypatch.setattr(sessions_module, "oauth", fake)
    cookie = store.create(tokens(expires_in=10), "user-1", "Dana")
    store.resolve(cookie)

    # Expire again and refresh a second time.
    with store.connection.cursor() as cursor:
        cursor.execute(
            "UPDATE oauth_sessions SET access_token_expires_at = now() - interval '1 minute'"
        )
    store.resolve(cookie)

    assert fake.calls == ["ort_first", "ort_renewed"]


def test_a_live_token_is_not_refreshed(store, monkeypatch):
    fake = FakeOAuth()
    monkeypatch.setattr(sessions_module, "oauth", fake)
    cookie = store.create(tokens(expires_in=3600), "user-1", "Dana")

    store.resolve(cookie)

    assert fake.calls == []


def test_a_session_whose_refresh_fails_is_signed_out(store, monkeypatch):
    monkeypatch.setattr(sessions_module, "oauth", FakeOAuth(error=OAuthError("revoked")))
    cookie = store.create(tokens(expires_in=10), "user-1", "Dana")

    assert store.resolve(cookie) is None
    assert row_for(store, cookie) is None


def test_an_expired_token_with_no_refresh_token_is_signed_out(store):
    cookie = store.create(tokens(refresh=None, expires_in=10), "user-1", "Dana")

    assert store.resolve(cookie) is None


def test_a_token_that_never_expires_is_left_alone(store, monkeypatch):
    """Outline may omit expires_in; nothing to refresh against."""
    fake = FakeOAuth()
    monkeypatch.setattr(sessions_module, "oauth", fake)
    cookie = store.create(tokens(expires_in=None), "user-1", "Dana")

    session = store.resolve(cookie)

    assert session.access_token == "oat_first"
    assert fake.calls == []


def test_the_session_secret_is_required_before_anything_is_stored(store, monkeypatch):
    monkeypatch.setattr(sessions_module.settings, "session_secret", "")

    with pytest.raises(sessions_module.SessionSecretMissing):
        store.create(tokens(), "user-1", "Dana")
