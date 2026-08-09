"""A search that returns nothing has to say whether that is the query or the setup.

`hash` and `intfloat/multilingual-e5-small` are both 384 dimensions, so every
dimension check passes while the query is compared against an index built by the
other model and matches nothing.
"""

from __future__ import annotations

import pytest
from opensql_autorag_api.embeddings import embedding_mismatch, get_embedding_provider
from opensql_autorag_api.settings import settings


class FakeRepository:
    def __init__(self, coverage: list[dict]) -> None:
        self._coverage = coverage

    def embedding_coverage(self) -> list[dict]:
        return self._coverage


@pytest.fixture(autouse=True)
def hash_provider(monkeypatch: pytest.MonkeyPatch):
    """Pin the process to the hash provider so no model is downloaded."""
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    get_embedding_provider.cache_clear()
    yield
    get_embedding_provider.cache_clear()


def test_empty_index_is_not_a_mismatch():
    """Nothing indexed yet is an empty index, not a misconfiguration."""
    assert embedding_mismatch(FakeRepository([]), 1) is None


def test_model_that_owns_the_index_is_not_a_mismatch():
    coverage = [{"embedding_model_id": 2, "provider": "hash", "model_name": "x", "chunk_count": 5}]

    assert embedding_mismatch(FakeRepository(coverage), 2) is None


def test_index_built_by_another_model_is_reported():
    coverage = [
        {
            "embedding_model_id": 1,
            "provider": "sentence-transformers",
            "model_name": "intfloat/multilingual-e5-small",
            "chunk_count": 23,
        }
    ]

    message = embedding_mismatch(FakeRepository(coverage), 2)

    assert message is not None
    # Names both sides, so the fix does not require guessing which is wrong.
    assert "hash/sha256-deterministic" in message
    assert "sentence-transformers/intfloat/multilingual-e5-small (23 chunks)" in message


def test_every_indexed_model_is_listed():
    coverage = [
        {"embedding_model_id": 1, "provider": "a", "model_name": "one", "chunk_count": 9},
        {"embedding_model_id": 3, "provider": "b", "model_name": "two", "chunk_count": 4},
    ]

    message = embedding_mismatch(FakeRepository(coverage), 2)

    assert "a/one (9 chunks)" in message
    assert "b/two (4 chunks)" in message


def test_coverage_reports_what_is_actually_stored(db_connection):
    """The query behind it agrees with the database, not just with a fake."""
    from opensql_autorag_api.repository import Repository

    coverage = Repository(db_connection).embedding_coverage()

    assert all(
        {"embedding_model_id", "provider", "model_name", "chunk_count"} <= row.keys()
        for row in coverage
    )


def test_coverage_ignores_retired_chunks(db_connection):
    """A model whose chunks were all superseded contributes nothing to a search.

    Counting its rows in chunk_embeddings would report coverage that no query can
    reach, which is the state left behind by switching models and re-indexing --
    and precisely when the mismatch most needs reporting.
    """
    from opensql_autorag_api.repository import Repository

    with db_connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT m.id,
                   count(*) FILTER (WHERE c.active) AS active
            FROM chunk_embeddings e
            JOIN document_chunks c ON c.id = e.chunk_id
            JOIN embedding_models m ON m.id = e.embedding_model_id
            GROUP BY m.id
            """
        )
        actual = {int(row["id"]): int(row["active"]) for row in cursor.fetchall()}

    reported = {
        int(row["embedding_model_id"]): int(row["chunk_count"])
        for row in Repository(db_connection).embedding_coverage()
    }

    assert reported == {model: count for model, count in actual.items() if count}
