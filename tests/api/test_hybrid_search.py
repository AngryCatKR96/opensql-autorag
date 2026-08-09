"""The keyword arm and the fusion of the two, against the real database.

Both arms filter by permission in SQL, so a scope that leaks through one of them
is exactly the failure a fake would hide.
"""

import hashlib
from uuid import UUID, uuid4

import pytest
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag_api.repository import Repository, SearchScope
from opensql_autorag_api.search import resolve_mode

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
        created = self.repository.create_document_version(
            title=name,
            source_type="md",
            source_path=f"/tmp/{name}.md",
            file_hash=hashlib.sha256(f"{name}{text}".encode()).hexdigest(),
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


@pytest.fixture
def seeded(db_connection):
    repository = Repository(db_connection)
    model_id = repository.resolve_embedding_model_id(
        provider="test-hybrid", model_name=f"probe-{uuid4()}", dimension=DIMENSION
    )
    fixture = Fixture(repository, model_id)
    # The token only the first document carries, in a document the vector arm
    # ranks last -- which is the whole reason the keyword arm exists.
    fixture.add(
        "carries-the-token",
        "ERRHNSWPROBE raised when an index scan stops early",
        collection_id="col-platform",
        weight=0.1,
    )
    fixture.add("nearest-vector", "an index scan that stops early", collection_id="col-platform")
    fixture.add(
        "out-of-scope",
        "ERRHNSWPROBE appears here too but nobody may read it",
        collection_id="col-secrets",
        weight=0.9,
    )
    return fixture


PLATFORM = SearchScope(allowed_collection_ids=("col-platform",), include_local_documents=False)


def titles(rows: list[dict]) -> list[str]:
    return [row["document_title"] for row in rows]


def test_keyword_arm_finds_a_literal_token(seeded):
    rows = seeded.repository.search_chunks_keyword("ERRHNSWPROBE", 10, PLATFORM)

    assert titles(rows) == ["carries-the-token"]


def test_keyword_arm_honours_the_caller_scope(seeded):
    """The permission filter has to hold on this arm too, not only on the vector one."""
    rows = seeded.repository.search_chunks_keyword("ERRHNSWPROBE", 10, PLATFORM)

    assert "out-of-scope" not in titles(rows)


def test_keyword_arm_ignores_words_that_are_not_there(seeded):
    assert seeded.repository.search_chunks_keyword("kingfisher", 10, PLATFORM) == []


def test_fusion_lifts_what_only_the_keyword_arm_ranks(seeded):
    """The document the vector arm puts last wins once both arms are counted."""
    vector_only = seeded.repository.search_chunks(vector(1.0), 10, seeded.model_id, PLATFORM)
    assert titles(vector_only)[0] == "nearest-vector"

    fused = seeded.repository.search_chunks_hybrid(
        "ERRHNSWPROBE", vector(1.0), 10, seeded.model_id, PLATFORM
    )

    assert titles(fused)[0] == "carries-the-token"


def test_fusion_reports_which_arms_matched(seeded):
    fused = seeded.repository.search_chunks_hybrid(
        "ERRHNSWPROBE", vector(1.0), 10, seeded.model_id, PLATFORM
    )
    by_title = {row["document_title"]: row for row in fused}

    assert sorted(by_title["carries-the-token"]["matched_by"]) == ["keyword", "vector"]
    assert by_title["nearest-vector"]["matched_by"] == ["vector"]
    # Each arm's own score survives fusion, so a caller can see what it said.
    assert by_title["carries-the-token"]["keyword_score"] > 0
    assert by_title["nearest-vector"]["keyword_score"] is None


def test_fusion_never_leaks_across_the_scope(seeded):
    """Neither arm may introduce a document the caller cannot read."""
    fused = seeded.repository.search_chunks_hybrid(
        "ERRHNSWPROBE", vector(0.9), 10, seeded.model_id, PLATFORM
    )

    assert "out-of-scope" not in titles(fused)


def test_hybrid_scores_rank_by_reciprocal_rank(seeded):
    """Fusion is on rank, so scores are bounded by the number of arms."""
    fused = seeded.repository.search_chunks_hybrid(
        "ERRHNSWPROBE", vector(1.0), 10, seeded.model_id, PLATFORM
    )
    scores = [row["score"] for row in fused]

    assert scores == sorted(scores, reverse=True)
    # Two arms, best possible rank in each: 1/(60+1) twice.
    assert scores[0] <= 2 / 61


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError, match="unknown search mode"):
        resolve_mode("semantic")


@pytest.mark.parametrize("mode", ["hybrid", "vector", "keyword"])
def test_known_modes_are_accepted(mode):
    assert resolve_mode(mode) == mode


def test_iterative_scan_is_enabled_on_the_platforms_own_connection(
    db_connection, monkeypatch: pytest.MonkeyPatch
):
    """Without it a filtered search can stop before it has filled top_k.

    This goes through `get_connection` rather than the fixture, because applying
    the setting is what that function is for -- a connection opened any other way
    is expected not to have it. It is pointed at the database the suite already
    reached, so the test is about the function and not about which URL happens to
    be configured.

    Skipped on pgvector older than 0.8, which has no such setting; the platform
    still works there, it just cannot resume a scan.
    """
    from opensql_autorag_api.db import get_connection
    from opensql_autorag_api.settings import settings

    from tests.conftest import _database_url

    monkeypatch.setattr(settings, "database_url", _database_url())

    with get_connection() as connection, connection.cursor() as cursor:
        cursor.execute("LOAD 'vector'")
        try:
            cursor.execute("SHOW hnsw.iterative_scan")
        except Exception:  # noqa: BLE001 - the point is that older pgvector lacks it
            connection.rollback()
            pytest.skip("this pgvector has no hnsw.iterative_scan")
        assert cursor.fetchone()["hnsw.iterative_scan"] in {"strict_order", "relaxed_order"}


def test_keyword_arm_does_not_need_every_term_present(seeded):
    """AND semantics would discard a match over one absent word.

    "ERRHNSWPROBE ... early" holds the identifier and all but the last word of
    this query. Requiring every term, which is what websearch_to_tsquery and
    plainto_tsquery both do, would return nothing here -- so the arm meant to
    carry the identifier would contribute nothing to fusion.
    """
    rows = seeded.repository.search_chunks_keyword(
        "ERRHNSWPROBE index scan behaviour on a saturday", 10, PLATFORM
    )

    assert "carries-the-token" in titles(rows)


def test_keyword_rank_prefers_the_chunk_with_more_of_the_terms(seeded):
    """OR widens what matches, so ranking is what has to discriminate."""
    rows = seeded.repository.search_chunks_keyword("ERRHNSWPROBE index scan", 10, PLATFORM)

    # Both documents contain "index scan"; only one also carries the identifier.
    assert titles(rows)[0] == "carries-the-token"


def test_keyword_arm_survives_punctuation_in_the_query(seeded):
    """Raw input reaches to_tsvector, never to_tsquery, so this cannot raise."""
    assert seeded.repository.search_chunks_keyword("what?! & | ( ) :*", 10, PLATFORM) == []
