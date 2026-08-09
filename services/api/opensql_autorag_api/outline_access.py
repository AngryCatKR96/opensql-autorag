from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

import httpx

from opensql_autorag_api.repository import SearchScope
from opensql_autorag_api.settings import settings

logger = logging.getLogger(__name__)


class OutlineAccessError(Exception):
    """A caller's Outline token could not be turned into a search scope."""


class InvalidOutlineToken(OutlineAccessError):
    """Outline rejected the token."""


class OutlineUnavailable(OutlineAccessError):
    """Outline could not be reached, so the caller's access is unknown."""


@dataclass(frozen=True)
class OutlineIdentity:
    """Who the caller is in Outline, and which collections they can read."""

    user_id: str
    user_name: str
    collection_ids: tuple[str, ...]

    def scope(self) -> SearchScope:
        return SearchScope(
            allowed_collection_ids=self.collection_ids,
            include_local_documents=True,
        )


class OutlineAccessResolver:
    """Resolves an Outline API token to the collections its owner can read.

    The token belongs to the caller, not to this service, so what it can see is
    exactly what its owner can see -- there is no admin credential here that
    could be talked into reading more. `collections.list` returns the caller's
    accessible collections (`server/routes/api/collections/collections.ts`
    filters on `user.collectionIds()`), and `documents.list` in Outline scopes
    documents the same way, so a collection allowlist is no more permissive than
    Outline's own document listing.

    Only the resolved scope is cached, keyed by a digest of the token; the token
    itself is never stored. A short TTL bounds how long a revoked membership
    keeps working.
    """

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._cache: dict[str, tuple[float, OutlineIdentity]] = {}
        self._lock = threading.Lock()
        # Only set in tests, to answer as an Outline instance would.
        self._transport = transport

    def _cached(self, key: str) -> OutlineIdentity | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            expires_at, identity = entry
            if expires_at < time.monotonic():
                del self._cache[key]
                return None
            return identity

    def _store(self, key: str, identity: OutlineIdentity) -> None:
        with self._lock:
            self._cache[key] = (
                time.monotonic() + settings.access_cache_seconds,
                identity,
            )

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def resolve(self, token: str) -> OutlineIdentity:
        if not token:
            raise InvalidOutlineToken("no Outline token was presented")

        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        cached = self._cached(key)
        if cached is not None:
            return cached

        identity = self._fetch(token)
        self._store(key, identity)
        logger.info(
            "resolved Outline access for %s: %d collection(s)",
            identity.user_id,
            len(identity.collection_ids),
        )
        return identity

    def _fetch(self, token: str) -> OutlineIdentity:
        base_url = settings.outline_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                base_url=base_url,
                headers=headers,
                timeout=settings.outline_timeout_seconds,
                transport=self._transport,
            ) as client:
                user = _post(client, "auth.info", {})["data"]["user"]
                collection_ids = _collection_ids(client)
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code in (401, 403):
                raise InvalidOutlineToken("Outline rejected the token") from exc
            raise OutlineUnavailable(f"Outline returned an error: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OutlineUnavailable(f"could not reach Outline: {exc}") from exc

        return OutlineIdentity(
            user_id=str(user["id"]),
            user_name=str(user.get("name") or ""),
            collection_ids=collection_ids,
        )


def _post(client: httpx.Client, endpoint: str, payload: dict) -> dict:
    response = client.post(f"/api/{endpoint}", json=payload)
    response.raise_for_status()
    return response.json()


def _collection_ids(client: httpx.Client) -> tuple[str, ...]:
    """Every collection the token's owner can read.

    `includeListOnly` and `statusFilter` are deliberately not sent: Outline skips
    the membership filter for an admin who asks for either, which would hand back
    collections the caller cannot actually read.
    """
    ids: list[str] = []
    offset = 0
    limit = settings.outline_page_size
    while True:
        page = _post(client, "collections.list", {"offset": offset, "limit": limit})["data"]
        ids.extend(str(collection["id"]) for collection in page)
        if len(page) < limit:
            return tuple(ids)
        offset += len(page)


resolver = OutlineAccessResolver()
