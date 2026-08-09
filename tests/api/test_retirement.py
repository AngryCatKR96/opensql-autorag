"""Retiring a document that was removed at its source, against the real database.

The guarantee under test is that retirement sticks: an indexing job that was
already queued when the document was removed must not bring it back.
"""

import hashlib
from uuid import uuid4

import pytest
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag_api.repository import Repository, SearchScope

DIMENSION = 384
SCOPE = SearchScope(allowed_collection_ids=("col-platform",))


def vector() -> list[float]:
    return [1.0] + [0.0] * (DIMENSION - 1)


def chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        stable_key=f"key-{index}",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        chunk_index=index,
        location=SourceLocation(heading_path=("Top",)),
        token_estimate=2,
    )


class Wiki:
    """One synced wiki document, and the operations a connector performs on it."""

    def __init__(self, repository: Repository, model_id: int) -> None:
        self.repository = repository
        self.model_id = model_id
        self.document_id = None

    def sync(self, text: str) -> object:
        """Index a new version of the document, as an ingest plus a worker run would."""
        created = self.repository.create_document_version(
            title="runbook",
            source_type="md",
            source_path="/tmp/runbook.md",
            file_hash=hashlib.sha256(text.encode()).hexdigest(),
            document_id=self.document_id,
        )
        self.document_id = created.document_id
        self.repository.upsert_document_source(
            document_id=created.document_id,
            source_system="outline",
            external_id=str(created.document_id),
            external_url="https://wiki.example.com/doc/runbook",
            external_updated_at=None,
            collection_id="col-platform",
            last_file_hash="hash",
        )
        return created

    def index(self, created: object, text: str) -> None:
        self.repository.insert_chunk_with_embedding(
            document_id=created.document_id,
            version_id=created.version_id,
            chunk=chunk(text),
            embedding=vector(),
            embedding_model_id=self.model_id,
        )
        self.repository.complete_indexing(
            job_id=created.job_id,
            document_id=created.document_id,
            version_id=created.version_id,
            reused_count=0,
            embedded_count=1,
            retired_count=0,
            elapsed_ms=1,
        )

    def is_searchable(self) -> bool:
        rows = self.repository.search_chunks(
            query_embedding=vector(),
            top_k=10,
            embedding_model_id=self.model_id,
            scope=SCOPE,
        )
        return any(row["document_id"] == self.document_id for row in rows)

    def job_status(self, job_id) -> str:
        with self.repository.connection.cursor() as cursor:
            cursor.execute("SELECT status FROM index_jobs WHERE id = %s", (job_id,))
            return cursor.fetchone()["status"]


@pytest.fixture
def wiki(db_connection):
    repository = Repository(db_connection)
    model_id = repository.resolve_embedding_model_id(
        provider="test-retire", model_name=f"probe-{uuid4()}", dimension=DIMENSION
    )
    page = Wiki(repository, model_id)
    created = page.sync("vector search runbook")
    page.index(created, "vector search runbook")
    assert page.is_searchable()
    return page


def test_retiring_a_document_takes_it_out_of_search(wiki):
    deactivated = wiki.repository.deactivate_document(wiki.document_id)

    assert deactivated == 1
    assert not wiki.is_searchable()
    assert wiki.repository.is_retired(wiki.document_id)


def test_retiring_keeps_the_chunks_for_a_later_restore(wiki):
    wiki.repository.deactivate_document(wiki.document_id)

    with wiki.repository.connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) AS kept FROM document_chunks WHERE document_id = %s",
            (wiki.document_id,),
        )
        assert cursor.fetchone()["kept"] == 1


def test_a_queued_job_is_cancelled_when_the_document_is_removed(wiki):
    queued = wiki.sync("an edit that was still being indexed")

    wiki.repository.deactivate_document(wiki.document_id)

    assert wiki.job_status(queued.job_id) == "cancelled"


def test_a_job_that_completes_after_removal_does_not_bring_the_document_back(wiki):
    """The race the retired flag exists for."""
    in_flight = wiki.sync("an edit that was still being indexed")
    wiki.repository.deactivate_document(wiki.document_id)

    wiki.index(in_flight, "an edit that was still being indexed")

    assert not wiki.is_searchable()


def test_a_restored_document_is_searchable_again_after_reindexing(wiki):
    wiki.repository.deactivate_document(wiki.document_id)

    assert wiki.repository.reactivate_document(wiki.document_id)
    restored = wiki.sync("vector search runbook")
    wiki.index(restored, "vector search runbook")

    assert wiki.is_searchable()
    assert not wiki.repository.is_retired(wiki.document_id)


def test_clearing_the_flag_alone_does_not_make_a_document_searchable(wiki):
    """Chunks come back through indexing, because the body may have changed."""
    wiki.repository.deactivate_document(wiki.document_id)

    wiki.repository.reactivate_document(wiki.document_id)

    assert not wiki.is_searchable()


def test_reactivating_a_document_that_was_not_retired_reports_nothing_to_do(wiki):
    assert wiki.repository.reactivate_document(wiki.document_id) is False


def test_retiring_an_unknown_document_is_not_an_error(wiki):
    assert wiki.repository.deactivate_document(uuid4()) == 0
