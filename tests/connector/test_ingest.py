"""Ingest decisions that depend on database state, against the real database.

The cases here are the ones where "the body did not change" is not enough to
decide what to do: a document that moved collection, and one that was removed at
the source and has come back.
"""

from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from opensql_autorag_api.repository import Repository
from opensql_autorag_connector import ingest as ingest_module
from opensql_autorag_connector.client import OutlineDocument
from opensql_autorag_connector.ingest import (
    IngestOutcome,
    ingest_document,
    is_unchanged,
    retire_document,
)

BODY = "# Runbook\n\nHow to rebuild the index."


def wiki_document(
    document_id: str,
    collection_id: str = "col-platform",
    text: str | None = BODY,
    updated_at: str = "2026-08-07T00:00:00.000Z",
) -> OutlineDocument:
    return OutlineDocument(
        id=document_id,
        title="Runbook",
        updated_at=updated_at,
        collection_id=collection_id,
        url=f"https://wiki.example.com/doc/{document_id}",
        text=text,
    )


@pytest.fixture
def wiki(db_connection, tmp_path, monkeypatch):
    """Ingest wired to the rolled-back test connection and a throwaway store."""

    @contextmanager
    def shared_connection():
        # Deliberately does not commit: everything this test writes is rolled back
        # with the fixture's transaction.
        yield db_connection

    monkeypatch.setattr(ingest_module, "get_connection", shared_connection)
    monkeypatch.setattr(ingest_module.api_settings, "storage_dir", tmp_path)
    return Repository(db_connection)


def test_a_first_sync_creates_a_version(wiki):
    result = ingest_document(wiki_document(str(uuid4())))

    assert result.outcome == IngestOutcome.INGESTED
    assert result.job_id is not None


def test_an_unchanged_document_is_not_re_versioned(wiki):
    document = wiki_document(str(uuid4()))
    ingest_document(document)

    result = ingest_document(document)

    assert result.outcome == IngestOutcome.SKIPPED
    assert result.job_id is None


def test_a_move_updates_the_collection_even_though_the_body_is_identical(wiki):
    """The collection is what search filters permissions on, so it cannot go stale."""
    document_id = str(uuid4())
    ingest_document(wiki_document(document_id, collection_id="col-open"))

    result = ingest_document(wiki_document(document_id, collection_id="col-restricted"))

    assert result.outcome == IngestOutcome.SKIPPED
    source = wiki.get_document_source(UUID(document_id))
    assert source["collection_id"] == "col-restricted"


def test_a_moved_document_is_not_reported_unchanged(wiki):
    """So a backfill fetches it rather than skipping it on updatedAt alone."""
    document_id = str(uuid4())
    ingest_document(wiki_document(document_id, collection_id="col-open"))

    assert is_unchanged(wiki_document(document_id, collection_id="col-open"))
    assert not is_unchanged(wiki_document(document_id, collection_id="col-restricted"))


def test_a_restored_document_is_re_indexed_even_with_an_identical_body(wiki):
    """Clearing the flag is not enough: an indexing job is what reactivates chunks."""
    document_id = str(uuid4())
    ingest_document(wiki_document(document_id))
    retire_document(document_id)

    result = ingest_document(wiki_document(document_id))

    assert result.outcome == IngestOutcome.INGESTED
    assert result.job_id is not None
    assert not wiki.is_retired(UUID(document_id))


def test_a_retired_document_is_not_reported_unchanged(wiki):
    document_id = str(uuid4())
    ingest_document(wiki_document(document_id))
    retire_document(document_id)

    assert not is_unchanged(wiki_document(document_id))


def test_an_empty_body_is_not_stored(wiki):
    result = ingest_document(wiki_document(str(uuid4()), text="   "))

    assert result.outcome == IngestOutcome.EMPTY


def test_retiring_a_document_that_was_never_synced_reports_nothing_to_do(wiki):
    assert retire_document(str(uuid4())) is False


def test_retiring_a_synced_document_reports_that_it_happened(wiki):
    document_id = str(uuid4())
    ingest_document(wiki_document(document_id))

    assert retire_document(document_id) is True
    assert wiki.is_retired(UUID(document_id))
