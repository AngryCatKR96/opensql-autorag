from __future__ import annotations

import json
import logging

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from opensql_autorag_connector.client import OutlineClient
from opensql_autorag_connector.ingest import ingest_document, retire_document
from opensql_autorag_connector.settings import settings
from opensql_autorag_connector.signature import SignatureError, verify_signature

logger = logging.getLogger(__name__)

app = FastAPI(title="OpenSQL AutoRAG Outline Connector")

# Events after which a document must stop being searchable. Taken from
# DocumentEvent in Outline's `server/types.ts`. `unpublish` belongs here because
# it turns a document back into a draft, which only its author may read.
REMOVAL_EVENTS = {
    "documents.delete",
    "documents.permanent_delete",
    "documents.archive",
    "documents.unpublish",
}

# Carries no document id: it fires once for the whole trash, whose documents each
# raised documents.delete when they went in.
EMPTY_TRASH_EVENT = "documents.empty_trash"


def _document_id(payload: dict) -> str | None:
    """Pull the document id out of a webhook payload.

    Outline nests the mutated model differently per event, so both the flat id
    and the model object are accepted.
    """
    if isinstance(payload.get("id"), str):
        return payload["id"]
    model = payload.get("model")
    if isinstance(model, dict) and isinstance(model.get("id"), str):
        return model["id"]
    document_id = payload.get("documentId")
    return document_id if isinstance(document_id, str) else None


def sync_document(document_id: str) -> None:
    """Fetch a document from Outline and queue it for indexing."""
    try:
        with OutlineClient(settings) as client:
            document = client.get_document(document_id)
        result = ingest_document(document)
    except httpx.HTTPError as exc:
        logger.warning("could not fetch document %s: %s", document_id, exc)
        return
    except Exception:
        logger.exception("failed to ingest document %s", document_id)
        return
    logger.info("document %s: %s", document_id, result.outcome)


def deactivate_document(document_id: str) -> None:
    """Stop serving a document that was removed in Outline.

    No call back to Outline: the document may already be gone there, and the
    event itself is authenticated proof that it was removed.
    """
    try:
        retired = retire_document(document_id)
    except Exception:
        logger.exception("failed to retire document %s", document_id)
        return
    if retired:
        logger.info("document %s: retired", document_id)
    else:
        logger.info("document %s: not synced from here, nothing to retire", document_id)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/outline/webhook")
async def receive_webhook(request: Request, background: BackgroundTasks) -> dict[str, str]:
    """Accept an Outline webhook.

    Outline expects a 200 within 5 seconds, so the document is fetched and
    indexed after the response is sent.
    """
    body = await request.body()
    try:
        verify_signature(
            body=body,
            header=request.headers.get("outline-signature"),
            secret=settings.webhook_secret,
            tolerance_seconds=settings.webhook_tolerance_seconds,
        )
    except SignatureError as exc:
        logger.warning("rejected webhook: %s", exc)
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    try:
        event = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc

    name = str(event.get("event") or "")
    if not name.startswith("documents."):
        return {"status": "ignored", "event": name}
    if name == EMPTY_TRASH_EVENT:
        return {"status": "ignored", "event": name}

    document_id = _document_id(event.get("payload") or {})
    if not document_id:
        raise HTTPException(status_code=400, detail="no document id in payload")

    if name in REMOVAL_EVENTS:
        background.add_task(deactivate_document, document_id)
        return {"status": "retiring", "event": name, "document_id": document_id}

    # Everything else -- update, restore, unarchive, move, a documents.* event
    # this connector has not seen before -- is handled by re-fetching the
    # document. A document that is no longer readable is logged and skipped.
    background.add_task(sync_document, document_id)
    return {"status": "accepted", "event": name, "document_id": document_id}
