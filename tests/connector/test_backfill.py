from uuid import UUID

import httpx
import pytest
from opensql_autorag_connector import backfill as backfill_module
from opensql_autorag_connector.backfill import run_backfill
from opensql_autorag_connector.client import OutlineDocument
from opensql_autorag_connector.ingest import IngestOutcome, IngestResult


def doc_id(label: str) -> str:
    """A stable Outline-shaped id for a one-letter label used in a test."""
    return f"0f1a4c8e-3c9d-4a1e-9b6f-2f0d5c7a{ord(label):04x}"


def listed(label: str, collection: str = "col-1") -> OutlineDocument:
    return OutlineDocument(
        id=doc_id(label),
        title=f"Doc {label}",
        updated_at="2026-08-07T00:00:00.000Z",
        collection_id=collection,
        url=f"https://wiki.example.com/doc/{label}",
    )


class FakeClient:
    """Stands in for OutlineClient, recording which bodies were fetched."""

    def __init__(self, by_collection: dict[str | None, list[OutlineDocument]]) -> None:
        self.by_collection = by_collection
        self.fetched: list[str] = []
        self.broken: set[str] = set()

    def iter_documents(self, collection_id: str | None = None):
        yield from self.by_collection.get(collection_id, [])

    def get_document(self, document_id: str) -> OutlineDocument:
        self.fetched.append(document_id)
        if document_id in self.broken:
            raise httpx.HTTPStatusError(
                "403", request=httpx.Request("POST", "/api/documents.info"), response=None
            )
        document = next(
            doc
            for docs in self.by_collection.values()
            for doc in docs
            if doc.id == document_id
        )
        return OutlineDocument(**{**document.__dict__, "text": f"# {document.title}\n\nbody"})


class Stubs:
    """Stands in for everything in a backfill that would reach the database."""

    def __init__(self) -> None:
        self.ingested: list[str] = []
        self.unchanged: set[str] = set()
        self.synced: set[UUID] = set()
        self.retired: list[str] = []


@pytest.fixture
def stubbed(monkeypatch):
    stubs = Stubs()

    def fake_is_unchanged(document: OutlineDocument) -> bool:
        return document.id in stubs.unchanged

    def fake_ingest(document: OutlineDocument, force: bool = False) -> IngestResult:
        stubs.ingested.append(document.id)
        return IngestResult(outcome=IngestOutcome.INGESTED)

    monkeypatch.setattr(backfill_module, "is_unchanged", fake_is_unchanged)
    monkeypatch.setattr(backfill_module, "ingest_document", fake_ingest)
    monkeypatch.setattr(
        backfill_module, "fetch_synced_document_ids", lambda ids=(): set(stubs.synced)
    )
    monkeypatch.setattr(backfill_module, "retire_document", stubs.retired.append)
    return stubs


def test_backfill_walks_every_configured_collection(stubbed):
    ingested = stubbed.ingested
    client = FakeClient({"col-1": [listed("a")], "col-2": [listed("b", "col-2")]})

    counts = run_backfill(client, ["col-1", "col-2"])

    assert counts["scanned"] == 2
    assert counts[IngestOutcome.INGESTED] == 2
    assert sorted(ingested) == [doc_id("a"), doc_id("b")]


def test_backfill_without_a_collection_filter_lists_everything(stubbed):
    ingested = stubbed.ingested
    client = FakeClient({None: [listed("a"), listed("b")]})

    counts = run_backfill(client, [])

    assert counts["scanned"] == 2
    assert sorted(ingested) == [doc_id("a"), doc_id("b")]


def test_unchanged_documents_are_skipped_without_fetching_the_body(stubbed):
    ingested, unchanged = stubbed.ingested, stubbed.unchanged
    unchanged.add(doc_id("a"))
    client = FakeClient({"col-1": [listed("a"), listed("b")]})

    counts = run_backfill(client, ["col-1"])

    assert counts[IngestOutcome.SKIPPED] == 1
    assert counts[IngestOutcome.INGESTED] == 1
    assert client.fetched == [doc_id("b")]
    assert ingested == [doc_id("b")]


def test_force_re_ingests_documents_that_look_unchanged(stubbed):
    ingested, unchanged = stubbed.ingested, stubbed.unchanged
    unchanged.add(doc_id("a"))
    client = FakeClient({"col-1": [listed("a")]})

    counts = run_backfill(client, ["col-1"], force=True)

    assert counts[IngestOutcome.INGESTED] == 1
    assert ingested == [doc_id("a")]


def test_one_unreadable_document_does_not_stop_the_sync(stubbed):
    ingested = stubbed.ingested
    client = FakeClient({"col-1": [listed("a"), listed("b")]})
    client.broken.add(doc_id("a"))

    counts = run_backfill(client, ["col-1"])

    assert counts["failed"] == 1
    assert counts[IngestOutcome.INGESTED] == 1
    assert ingested == [doc_id("b")]


def test_a_document_outline_no_longer_lists_is_retired(stubbed):
    """Recovers from a delete whose webhook never arrived."""
    stubbed.synced = {UUID(doc_id("a")), UUID(doc_id("g"))}
    client = FakeClient({"col-1": [listed("a")]})

    counts = run_backfill(client, ["col-1"])

    assert counts["retired"] == 1
    assert stubbed.retired == [doc_id("g")]


def test_documents_still_listed_are_not_retired(stubbed):
    stubbed.synced = {UUID(doc_id("a")), UUID(doc_id("b"))}
    client = FakeClient({"col-1": [listed("a"), listed("b")]})

    counts = run_backfill(client, ["col-1"])

    assert counts["retired"] == 0
    assert stubbed.retired == []


def test_a_partial_listing_retires_nothing(stubbed):
    """A failed fetch means the listing is not evidence that anything is gone."""
    stubbed.synced = {UUID(doc_id("a")), UUID(doc_id("z"))}
    client = FakeClient({"col-1": [listed("a")]})
    client.broken.add(doc_id("a"))

    counts = run_backfill(client, ["col-1"])

    assert counts["failed"] == 1
    assert counts["retired"] == 0
    assert stubbed.retired == []


def test_no_prune_keeps_documents_outline_stopped_listing(stubbed):
    stubbed.synced = {UUID(doc_id("z"))}
    client = FakeClient({"col-1": [listed("a")]})

    counts = run_backfill(client, ["col-1"], prune=False)

    assert counts["retired"] == 0
    assert stubbed.retired == []
