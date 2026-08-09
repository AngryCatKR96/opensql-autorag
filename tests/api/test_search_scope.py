"""What a search may return, exercised against the real database.

The permission filter lives in SQL, so these run against Postgres rather than a
fake: a filter that is wrong in a way Python cannot see is exactly the failure
worth catching.
"""

import hashlib
from uuid import UUID, uuid4

import pytest
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag_api.repository import Repository, SearchScope

DIMENSION = 384


def vector(weight: float) -> list[float]:
    """A vector whose distance from `vector(1.0)` grows as `weight` falls."""
    return [weight] + [0.0] * (DIMENSION - 1)


def chunk(text: str, index: int = 0) -> Chunk:
    return Chunk(
        stable_key=f"key-{index}-{text}",
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        chunk_index=index,
        location=SourceLocation(heading_path=("Top",)),
        token_estimate=len(text.split()),
    )


class Fixture:
    """Documents seeded for one test, and the ids needed to assert on them."""

    def __init__(self, repository: Repository, model_id: int) -> None:
        self.repository = repository
        self.model_id = model_id
        self.documents: dict[str, UUID] = {}

    def add(
        self,
        name: str,
        text: str,
        collection_id: str | None,
        source_system: str | None = "outline",
        weight: float = 1.0,
    ) -> UUID:
        """Seed one indexed document, with or without an external source."""
        created = self.repository.create_document_version(
            title=name,
            source_type="md",
            source_path=f"/tmp/{name}.md",
            file_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
        if source_system is not None:
            self.repository.upsert_document_source(
                document_id=created.document_id,
                source_system=source_system,
                external_id=str(created.document_id),
                external_url=f"https://wiki.example.com/doc/{name}",
                external_updated_at=None,
                collection_id=collection_id,
                last_file_hash="hash",
            )
        self.repository.insert_chunk_with_embedding(
            document_id=created.document_id,
            version_id=created.version_id,
            chunk=chunk(text),
            embedding=vector(weight),
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
        self.documents[name] = created.document_id
        return created.document_id

    def search(self, scope: SearchScope, top_k: int = 10) -> set[str]:
        """Titles of the documents a search in this scope reaches."""
        rows = self.repository.search_chunks(
            query_embedding=vector(1.0),
            top_k=top_k,
            embedding_model_id=self.model_id,
            scope=scope,
        )
        return {row["document_title"] for row in rows}


@pytest.fixture
def seeded(db_connection):
    repository = Repository(db_connection)
    model_id = repository.resolve_embedding_model_id(
        provider="test-scope", model_name=f"probe-{uuid4()}", dimension=DIMENSION
    )
    fixture = Fixture(repository, model_id)
    fixture.add("in-platform", "vector search runbook", collection_id="col-platform")
    fixture.add("in-secrets", "board compensation review", collection_id="col-secrets")
    fixture.add("no-collection", "an unpublished draft", collection_id=None)
    fixture.add("uploaded", "a locally uploaded pdf", collection_id=None, source_system=None)
    return fixture


def test_only_the_callers_collections_are_reachable(seeded):
    found = seeded.search(SearchScope(allowed_collection_ids=("col-platform",)))

    assert found == {"in-platform", "uploaded"}


def test_a_collection_the_caller_cannot_read_stays_invisible(seeded):
    found = seeded.search(SearchScope(allowed_collection_ids=("col-platform",)))

    assert "in-secrets" not in found


def test_an_externally_sourced_document_with_no_collection_is_unreachable(seeded):
    """A draft has no collection, so no collection grant can reach it."""
    everything = seeded.search(
        SearchScope(allowed_collection_ids=("col-platform", "col-secrets"))
    )

    assert "no-collection" not in everything


def test_a_caller_with_no_collections_still_sees_local_uploads(seeded):
    found = seeded.search(SearchScope.local_only())

    assert found == {"uploaded"}


def test_local_uploads_can_be_excluded_too(seeded):
    found = seeded.search(
        SearchScope(allowed_collection_ids=("col-platform",), include_local_documents=False)
    )

    assert found == {"in-platform"}


def test_a_second_collection_widens_the_result(seeded):
    found = seeded.search(SearchScope(allowed_collection_ids=("col-platform", "col-secrets")))

    assert found == {"in-platform", "in-secrets", "uploaded"}


def test_an_out_of_scope_document_does_not_consume_a_top_k_slot(seeded):
    """The filter is applied in the query, not to its results."""
    # in-secrets is the nearest match, but out of scope; asking for one result
    # must still return the nearest *reachable* one rather than nothing.
    seeded.add("nearest-secret", "closest hit", collection_id="col-secrets", weight=1.0)

    found = seeded.search(SearchScope(allowed_collection_ids=("col-platform",)), top_k=1)

    assert len(found) == 1
    assert found.issubset({"in-platform", "uploaded"})


def test_listing_documents_is_scoped_the_same_way(seeded):
    titles = {
        row["title"]
        for row in seeded.repository.list_documents(
            SearchScope(allowed_collection_ids=("col-platform",))
        )
    }

    assert "in-platform" in titles
    assert "in-secrets" not in titles
    assert "no-collection" not in titles


def test_chunk_context_is_scoped_so_a_chunk_id_is_not_a_capability(seeded):
    in_scope = SearchScope(allowed_collection_ids=("col-secrets",))
    rows = seeded.repository.search_chunks(
        query_embedding=vector(1.0), top_k=10, embedding_model_id=seeded.model_id, scope=in_scope
    )
    secret_chunk = next(row["chunk_id"] for row in rows if row["document_title"] == "in-secrets")

    denied = seeded.repository.get_chunk_context(
        secret_chunk, SearchScope(allowed_collection_ids=("col-platform",))
    )
    allowed = seeded.repository.get_chunk_context(secret_chunk, in_scope)

    assert denied == []
    assert [row["text"] for row in allowed] == ["board compensation review"]


def test_document_in_scope_matches_what_search_reaches(seeded):
    scope = SearchScope(allowed_collection_ids=("col-platform",))

    assert seeded.repository.document_in_scope(seeded.documents["in-platform"], scope)
    assert not seeded.repository.document_in_scope(seeded.documents["in-secrets"], scope)
