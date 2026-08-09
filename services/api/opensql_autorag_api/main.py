from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse

from opensql_autorag_api.db import get_connection
from opensql_autorag_api.embeddings import embedding_mismatch, get_embedding_provider
from opensql_autorag_api.oauth import OAuthError, OAuthNotConfigured, oauth
from opensql_autorag_api.outline_access import (
    InvalidOutlineToken,
    OutlineUnavailable,
    resolver,
)
from opensql_autorag_api.repository import Repository, SearchScope
from opensql_autorag_api.schemas import DocumentSummary, DocumentUploadResponse, SearchRequest
from opensql_autorag_api.search import execute_search, resolve_mode
from opensql_autorag_api.sessions import COOKIE_NAME, SessionStore
from opensql_autorag_api.settings import settings

logger = logging.getLogger(__name__)


def _report_embedding_mismatch() -> str | None:
    """Resolve the configured model against what is actually indexed."""
    provider = get_embedding_provider()
    with get_connection() as connection:
        repository = Repository(connection)
        embedding_model_id = repository.resolve_embedding_model_id(
            provider=settings.embedding_provider,
            model_name=provider.model_name,
            dimension=provider.dimension,
        )
        return embedding_mismatch(repository, embedding_model_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Say at boot what would otherwise only show up as empty search results.

    This does not refuse to start. Uploading and listing still work with a
    mismatched search model, and a database that is briefly unreachable at boot
    should not keep the process down.
    """
    with suppress(Exception):
        problem = _report_embedding_mismatch()
        if problem:
            logger.error("embedding configuration: %s", problem)
    yield


app = FastAPI(title="OpenSQL AutoRAG Sync", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/auth/outline/login")
def outline_login(request: Request, next: str = "/") -> RedirectResponse:
    """Send the caller to Outline to authorize this application.

    The PKCE verifier and the CSRF state are held server side for the round trip,
    so neither is exposed to the browser.
    """
    try:
        pending = oauth.begin()
    except OAuthNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with get_connection() as connection:
        store = SessionStore(connection)
        store.purge_expired()
        store.remember_login(pending.state, pending.code_verifier, _safe_next(next))
    return RedirectResponse(pending.authorization_url, status_code=307)


@app.get("/auth/outline/callback")
def outline_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Finish the login Outline just sent the caller back from."""
    if error:
        raise HTTPException(status_code=400, detail=f"Outline refused the login: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="callback is missing code or state")

    with get_connection() as connection:
        pending = SessionStore(connection).claim_login(state)
    if pending is None:
        # Unknown, already used, or expired: all mean this callback cannot be
        # trusted to belong to a login this service started.
        raise HTTPException(status_code=400, detail="unknown or expired login state")

    try:
        tokens = oauth.exchange(code, str(pending["code_verifier"]))
    except OAuthNotConfigured as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except OAuthError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Who the token belongs to, asked of Outline rather than taken on trust.
    try:
        identity = resolver.resolve(tokens.access_token)
    except InvalidOutlineToken as exc:
        raise HTTPException(
            status_code=502, detail=f"Outline issued a token it rejects: {exc}"
        ) from exc
    except OutlineUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    with get_connection() as connection:
        cookie_value = SessionStore(connection).create(tokens, identity.user_id, identity.user_name)

    destination = pending["redirect_after"] or "/"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        cookie_value,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return response


@app.post("/auth/outline/logout")
def outline_logout(request: Request, response: Response) -> dict[str, str]:
    """Drop the session here, and revoke the token at Outline."""
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        with get_connection() as connection:
            store = SessionStore(connection)
            session = store.resolve(cookie)
            store.delete(cookie)
        if session:
            oauth.revoke(session.access_token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "signed out"}


@app.get("/auth/outline/me")
def outline_me(request: Request) -> dict:
    """Who the console is signed in as, and whether signing in is even available."""
    cookie = request.cookies.get(COOKIE_NAME)
    session = None
    if cookie:
        with get_connection() as connection:
            session = SessionStore(connection).resolve(cookie)
    return {
        "login_available": oauth.configured,
        "outline_user": session.outline_user_id if session else None,
        "outline_user_name": session.outline_user_name if session else None,
    }


def _safe_next(destination: str) -> str:
    """Only allow redirecting back to a path on this site, never to another host."""
    if destination.startswith("/") and not destination.startswith("//"):
        return destination
    return "/"


@app.get("/documents", response_model=list[DocumentSummary])
def list_documents(http_request: Request) -> list[dict]:
    scope, _ = _resolve_scope(http_request)
    with get_connection() as connection:
        return Repository(connection).list_documents(scope)


async def _store_document_upload(
    file: UploadFile,
    document_id: UUID | None = None,
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in {"pdf", "docx", "md", "txt"}:
        raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}")

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    source_path = settings.storage_dir / f"{file_hash}.{suffix}"
    source_path.write_bytes(content)

    with get_connection() as connection:
        created = Repository(connection).create_document_version(
            title=file.filename,
            source_type=suffix,
            source_path=str(source_path),
            file_hash=file_hash,
            document_id=document_id,
        )

    return DocumentUploadResponse(
        document_id=created.document_id,
        version_id=created.version_id,
        job_id=created.job_id,
    )


@app.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: Annotated[UploadFile, File()]) -> DocumentUploadResponse:
    return await _store_document_upload(file)


@app.post("/documents/{document_id}/versions", response_model=DocumentUploadResponse)
async def upload_document_version(
    document_id: UUID,
    file: Annotated[UploadFile, File()],
) -> DocumentUploadResponse:
    return await _store_document_upload(file, document_id=document_id)


def _caller_token(request: Request) -> str:
    """The caller's own Outline API token, if they presented one.

    `X-Outline-Token` is the explicit form; a bearer token is accepted too so a
    client that already speaks to Outline can reuse its Authorization header.
    """
    explicit = request.headers.get("x-outline-token")
    if explicit:
        return explicit.strip()
    authorization = request.headers.get("authorization") or ""
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _session_token(request: Request) -> str:
    """The Outline access token of the signed-in caller, if there is one."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        return ""
    with get_connection() as connection:
        session = SessionStore(connection).resolve(cookie)
    return session.access_token if session else ""


def _resolve_scope(request: Request) -> tuple[SearchScope, dict]:
    """Work out what this caller may search, and how to describe that back.

    A token presented on the request wins over the session cookie: the header is
    a deliberate act by a machine caller, the cookie is ambient. A caller with
    neither is not treated as an anonymous member of the wiki — nothing that was
    synced from Outline is in scope for them, only documents uploaded straight
    into AutoRAG.
    """
    token = _caller_token(request) or _session_token(request)
    if not token:
        return SearchScope.local_only(), {"outline_user": None, "collection_count": 0}

    try:
        identity = resolver.resolve(token)
    except InvalidOutlineToken as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except OutlineUnavailable as exc:
        # The caller's access is unknown, so nothing from Outline can be served.
        # Answering from the local documents alone would look like an empty wiki.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return identity.scope(), {
        "outline_user": identity.user_id,
        "collection_count": len(identity.collection_ids),
    }


@app.post("/search")
def search_documents(request: SearchRequest, http_request: Request) -> dict:
    scope, applied_scope = _resolve_scope(http_request)
    try:
        mode = resolve_mode(request.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with get_connection() as connection:
        outcome = execute_search(
            Repository(connection),
            get_embedding_provider(),
            scope,
            applied_scope,
            request.query,
            request.top_k,
            mode,
        )
    if outcome.warning:
        logger.error("embedding configuration: %s", outcome.warning)
    return {
        "query": request.query,
        "top_k": request.top_k,
        "mode": outcome.mode,
        "embedding_model": outcome.embedding_model,
        "scope": outcome.scope,
        "results": outcome.rows,
        "warning": outcome.warning,
    }
