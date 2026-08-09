"""Read-only checks against a real Outline instance before syncing anything.

Pointing this at a wiki that is actually in use has failure modes that no amount
of local testing reaches: an API key with scopes that exclude an endpoint we
depend on, a base URL that resolves but is not Outline, a collection id that the
key cannot read. Every check here is a read, so it is safe to run against
production, and it names the endpoint that failed rather than reporting a generic
error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from opensql_autorag_connector.client import OutlineClient

# Endpoints the platform depends on, and what stops working without each. An
# Outline API key created with scopes may permit some and not others
# (`server/models/ApiKey.ts`, `canAccess`).
NEEDED_BY = {
    "auth.info": "resolving who a search caller is",
    "collections.list": "deciding which collections a caller may read",
    "documents.list": "finding documents to sync",
    "documents.info": "fetching document bodies",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def line(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        return f"[{mark}] {self.name}: {self.detail}"


@dataclass
class Preflight:
    checks: list[Check] = field(default_factory=list)
    collections: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def record(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append(Check(name=name, ok=ok, detail=detail))


def _why(endpoint: str, exc: Exception) -> str:
    """Explain a failed call in terms of what it costs, not just its status."""
    needed_for = NEEDED_BY.get(endpoint, "syncing")
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        status = exc.response.status_code
        if status == 401:
            return f"the API key was rejected — needed for {needed_for}"
        if status == 403:
            return (
                f"the API key is not allowed to call {endpoint}. An Outline API key "
                f"created with scopes must include it — needed for {needed_for}"
            )
        if status == 404:
            return (
                f"no {endpoint} at this base URL. Check AUTORAG_OUTLINE_BASE_URL "
                "points at the Outline root, without a trailing /api"
            )
        return f"{endpoint} returned {status} — needed for {needed_for}"
    return f"could not reach {endpoint}: {exc} — needed for {needed_for}"


def run_preflight(client: OutlineClient, collection_ids: tuple[str, ...] = ()) -> Preflight:
    """Check that this Outline instance and key support everything we need."""
    result = Preflight()

    try:
        identity = client.whoami()
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        result.record("auth.info", False, _why("auth.info", exc))
        return result

    user = identity.get("user") or {}
    team = identity.get("team") or {}
    result.record(
        "auth.info",
        True,
        f"authenticated as {user.get('name')} <{user.get('email', 'no email')}> "
        f"in workspace {team.get('name')!r}",
    )

    try:
        result.collections = client.list_collections()
    except httpx.HTTPError as exc:
        result.record("collections.list", False, _why("collections.list", exc))
        return result

    names = ", ".join(str(c.get("name")) for c in result.collections) or "none"
    result.record(
        "collections.list",
        True,
        f"{len(result.collections)} readable collection(s): {names}",
    )

    known_ids = {str(c["id"]) for c in result.collections}
    unknown = [cid for cid in collection_ids if cid not in known_ids]
    if unknown:
        # Checking documents in a scope this key cannot read would only report an
        # empty scope, which reads as "no documents yet" rather than "wrong ids".
        result.record(
            "sync scope",
            False,
            f"this key cannot read: {', '.join(unknown)}. "
            "Pass ids from the list above, not collection names",
        )
        return result
    if collection_ids:
        result.record("sync scope", True, f"{len(collection_ids)} collection(s) to sync")
    else:
        result.record(
            "sync scope",
            True,
            "no collection filter: every collection above would be synced. "
            "Set AUTORAG_OUTLINE_COLLECTIONS to narrow it",
        )

    sample = None
    try:
        scope = collection_ids or (None,)
        for collection_id in scope:
            for document in client.iter_documents(collection_id=collection_id):
                sample = document
                break
            if sample is not None:
                break
    except httpx.HTTPError as exc:
        result.record("documents.list", False, _why("documents.list", exc))
        return result

    if sample is None:
        result.record("documents.list", True, "reachable, but the scope holds no documents yet")
        return result
    result.record("documents.list", True, f"reachable; newest in scope is {sample.title!r}")

    try:
        fetched = client.get_document(sample.id)
    except httpx.HTTPError as exc:
        result.record("documents.info", False, _why("documents.info", exc))
        return result

    body_length = len(fetched.text or "")
    result.record(
        "documents.info",
        bool(body_length),
        f"fetched {body_length} characters of markdown for {fetched.title!r}"
        if body_length
        else f"{fetched.title!r} came back with no body, so nothing would be indexed",
    )
    return result
