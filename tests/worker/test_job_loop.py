from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag.hash_utils import content_hash, stable_key
from opensql_autorag_worker import main as worker_main
from opensql_autorag_worker.processor import CountingEmbeddingProvider, IndexProcessor

DOCUMENT_ID = uuid4()
VERSION_ID = uuid4()
JOB_ID = uuid4()
MODEL_ID = 7


class FakeRepository:
    """Records what the job loop writes, without touching a database."""

    def __init__(self, previous_chunks: tuple[Chunk, ...], reusable_hashes: set[str]) -> None:
        self.previous_chunks = previous_chunks
        self.reusable_hashes = reusable_hashes
        self.jobs = [{"id": JOB_ID, "document_id": DOCUMENT_ID, "version_id": VERSION_ID}]
        self.embedded: list[str] = []
        self.reused: list[str] = []
        self.completed: dict | None = None
        self.failure: str | None = None

    def embedding_column_dimension(self) -> int:
        return 384

    def resolve_embedding_model_id(self, provider: str, model_name: str, dimension: int) -> int:
        return MODEL_ID

    def claim_next_job(self) -> dict | None:
        return self.jobs.pop(0) if self.jobs else None

    def get_version_source_path(self, version_id: UUID) -> str:
        return str(self.source_path)

    def load_active_chunks(self, document_id: UUID) -> list[dict]:
        return [
            {
                "stable_key": chunk.stable_key,
                "text": chunk.text,
                "content_hash": chunk.content_hash,
                "chunk_index": chunk.chunk_index,
                "heading_path": "",
                "page_start": None,
                "page_end": None,
                "token_estimate": chunk.token_estimate,
            }
            for chunk in self.previous_chunks
        ]

    def has_reusable_embedding(
        self, document_id: UUID, content_hash: str, embedding_model_id: int
    ) -> bool:
        assert embedding_model_id == MODEL_ID
        return content_hash in self.reusable_hashes

    def insert_chunk_with_embedding(
        self, document_id, version_id, chunk, embedding, embedding_model_id
    ) -> UUID:
        assert embedding_model_id == MODEL_ID
        assert len(embedding) == 384
        self.embedded.append(chunk.text)
        return uuid4()

    def insert_chunk_reusing_embedding(
        self, document_id, version_id, chunk, embedding_model_id
    ) -> UUID:
        assert embedding_model_id == MODEL_ID
        self.reused.append(chunk.text)
        return uuid4()

    def complete_indexing(self, **kwargs) -> None:
        self.completed = kwargs

    def mark_job_failed(self, job_id: UUID, message: str) -> None:
        self.failure = message


@pytest.fixture
def run_job(monkeypatch, tmp_path: Path):
    @contextmanager
    def fake_connection():
        yield object()

    def run(text: str, previous_chunks: tuple[Chunk, ...], reusable_hashes: set[str]):
        repository = FakeRepository(previous_chunks, reusable_hashes)
        repository.source_path = tmp_path / "guide.txt"
        repository.source_path.write_text(text, encoding="utf-8")

        monkeypatch.setattr(worker_main, "get_connection", fake_connection)
        monkeypatch.setattr(worker_main, "Repository", lambda connection: repository)
        monkeypatch.setattr(
            worker_main, "get_embedding_provider", lambda: CountingEmbeddingProvider()
        )
        # One chunk per line, so a single test document produces several chunks.
        monkeypatch.setattr(
            worker_main,
            "IndexProcessor",
            lambda embedding_provider: IndexProcessor(
                embedding_provider=embedding_provider,
                chunker=SemanticChunker(target_tokens=2, overlap_tokens=0),
            ),
        )
        assert worker_main.process_next_job() is True
        assert repository.failure is None
        return repository

    return run


def make_chunk(index: int, text: str) -> Chunk:
    location = SourceLocation(heading_path=())
    return Chunk(
        stable_key=stable_key(str(DOCUMENT_ID), location.heading_path, index, text),
        text=text,
        content_hash=content_hash(text),
        chunk_index=index,
        location=location,
        token_estimate=len(text.split()),
    )


def test_retired_chunks_are_not_written_to_the_new_version(run_job):
    previous = (make_chunk(0, "kept text"), make_chunk(1, "dropped text"))
    reusable = {chunk.content_hash for chunk in previous}

    repository = run_job("kept text\nfresh text", previous, reusable)

    assert repository.reused == ["kept text"]
    assert repository.embedded == ["fresh text"]
    assert repository.completed["reused_count"] == 1
    assert repository.completed["embedded_count"] == 1
    assert repository.completed["retired_count"] == 1


def test_unchanged_chunk_is_re_embedded_when_the_model_has_no_vector(run_job):
    previous = (make_chunk(0, "kept text"),)

    repository = run_job("kept text", previous, reusable_hashes=set())

    assert repository.reused == []
    assert repository.embedded == ["kept text"]
    assert repository.completed["reused_count"] == 0
    assert repository.completed["embedded_count"] == 1
