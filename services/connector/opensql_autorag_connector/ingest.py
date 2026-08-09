from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
from opensql_autorag_api.settings import settings as api_settings

from opensql_autorag_connector.client import OutlineDocument

SOURCE_SYSTEM = "outline"


class IngestOutcome(StrEnum):
    INGESTED = "ingested"
    SKIPPED = "skipped"
    EMPTY = "empty"


@dataclass(frozen=True)
class IngestResult:
    outcome: IngestOutcome
    document_id: UUID | None = None
    version_id: UUID | None = None
    job_id: UUID | None = None


def _storage_path(document_id: UUID, file_hash: str) -> Path:
    directory = api_settings.storage_dir / SOURCE_SYSTEM
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{document_id}-{file_hash[:16]}.md"


def ingest_document(document: OutlineDocument, *, force: bool = False) -> IngestResult:
    """Store one Outline document as a new version and queue it for indexing.

    Outline document ids are UUIDs and are reused verbatim as AutoRAG document
    ids, so no id mapping is needed and a re-sync lands as a new version of the
    same document.
    """
    markdown = (document.text or "").strip()
    if not markdown:
        return IngestResult(outcome=IngestOutcome.EMPTY)

    document_id = UUID(document.id)
    content = markdown.encode("utf-8")
    file_hash = hashlib.sha256(content).hexdigest()

    with get_connection() as connection:
        repository = Repository(connection)
        known = repository.get_document_source(document_id)
        unchanged = known is not None and known["last_file_hash"] == file_hash
        # A document that was removed at the source and has come back has to go
        # through indexing again even if its body is byte for byte the same: that
        # is what makes its chunks active once more.
        retired = repository.is_retired(document_id)

        def record_source() -> None:
            repository.upsert_document_source(
                document_id=document_id,
                source_system=SOURCE_SYSTEM,
                external_id=document.id,
                external_url=document.url,
                external_updated_at=document.updated_at,
                collection_id=document.collection_id,
                last_file_hash=file_hash,
            )

        if unchanged and not force and not retired:
            # Creating another version would only add an indexing job whose
            # chunks are all reused. The metadata is still written, because a
            # move changes which collection a document belongs to without
            # touching its body -- and the collection is what search filters
            # permissions on.
            record_source()
            return IngestResult(outcome=IngestOutcome.SKIPPED, document_id=document_id)

        # Cleared before the job is queued, so that the job completing is what
        # activates the chunks.
        if retired:
            repository.reactivate_document(document_id)

        source_path = _storage_path(document_id, file_hash)
        source_path.write_bytes(content)

        created = repository.create_document_version(
            title=document.title,
            source_type="md",
            source_path=str(source_path),
            file_hash=file_hash,
            document_id=document_id,
        )
        record_source()

    return IngestResult(
        outcome=IngestOutcome.INGESTED,
        document_id=created.document_id,
        version_id=created.version_id,
        job_id=created.job_id,
    )


def is_unchanged(document: OutlineDocument) -> bool:
    """Whether a listed document is already synced at this `updatedAt`.

    Lets the backfill skip documents without fetching their body. A document that
    moved collection, or that was retired and is listed again, is never
    "unchanged": the first changes who may find it, the second whether it can be
    found at all, and neither shows up in `updatedAt`.
    """
    if document.updated_at is None:
        return False
    with get_connection() as connection:
        repository = Repository(connection)
        known = repository.get_document_source(UUID(document.id))
        if known is None or known["external_updated_at"] is None:
            return False
        if repository.is_retired(UUID(document.id)):
            return False
    if known["collection_id"] != document.collection_id:
        return False
    try:
        listed = datetime.fromisoformat(document.updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return known["external_updated_at"] == listed


def fetch_synced_document_ids(collection_ids: tuple[str, ...] = ()) -> set[UUID]:
    """Outline documents AutoRAG currently serves, within these collections."""
    with get_connection() as connection:
        return Repository(connection).synced_document_ids(SOURCE_SYSTEM, collection_ids)


def retire_document(document_id: str) -> bool:
    """Stop serving a document that was removed at the source.

    The chunks and versions are kept -- the wiki page may come back, and the
    stored embeddings are then reused -- but nothing is searchable while the
    document is retired.
    """
    with get_connection() as connection:
        repository = Repository(connection)
        parsed = UUID(document_id)
        if repository.get_document_source(parsed) is None:
            # Never synced from here, or already removed from AutoRAG entirely.
            return False
        repository.deactivate_document(parsed)
    return True
