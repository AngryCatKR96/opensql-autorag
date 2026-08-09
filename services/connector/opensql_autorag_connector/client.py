from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from opensql_autorag_connector.settings import OutlineSettings


@dataclass(frozen=True)
class OutlineDocument:
    """The fields of an Outline document this connector needs."""

    id: str
    title: str
    updated_at: str | None
    collection_id: str | None
    url: str | None
    text: str | None = None

    @classmethod
    def from_api(cls, data: dict) -> OutlineDocument:
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or "Untitled"),
            updated_at=data.get("updatedAt"),
            collection_id=data.get("collectionId"),
            url=data.get("url"),
            text=data.get("text"),
        )


class OutlineClient:
    """Minimal Outline API client.

    Outline's API is POST-only with the API key as a bearer token. Document
    listings carry metadata but no body, so the body comes from documents.info,
    which returns the markdown in `text` along with fresh metadata.
    """

    def __init__(self, settings: OutlineSettings, client: httpx.Client | None = None) -> None:
        if not settings.api_key:
            raise ValueError("AUTORAG_OUTLINE_API_KEY is not set")
        self.settings = settings
        self._client = client or httpx.Client(
            base_url=settings.base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=settings.timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OutlineClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _post(self, endpoint: str, payload: dict) -> dict:
        response = self._client.post(f"/api/{endpoint}", json=payload)
        response.raise_for_status()
        return response.json()

    def whoami(self) -> dict:
        """The user this API key authenticates as, and their workspace."""
        body = self._post("auth.info", {})
        return body["data"]

    def list_collections(self) -> list[dict]:
        collections: list[dict] = []
        offset = 0
        while True:
            body = self._post(
                "collections.list", {"offset": offset, "limit": self.settings.page_size}
            )
            page = body.get("data") or []
            collections.extend(page)
            if len(page) < self.settings.page_size:
                return collections
            offset += len(page)

    def iter_documents(self, collection_id: str | None = None) -> Iterator[OutlineDocument]:
        """Walk document metadata, newest first. No document bodies."""
        offset = 0
        while True:
            payload: dict = {
                "offset": offset,
                "limit": self.settings.page_size,
                "sort": "updatedAt",
                "direction": "DESC",
            }
            if collection_id:
                payload["collectionId"] = collection_id
            body = self._post("documents.list", payload)
            page = body.get("data") or []
            for item in page:
                yield OutlineDocument.from_api(item)
            if len(page) < self.settings.page_size:
                return
            offset += len(page)

    def get_document(self, document_id: str) -> OutlineDocument:
        """Metadata plus the markdown body of one document."""
        body = self._post("documents.info", {"id": document_id})
        return OutlineDocument.from_api(body["data"])
