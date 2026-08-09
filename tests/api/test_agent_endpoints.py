"""The two endpoints the MCP server needs, over HTTP against the real database.

These carry the same permission filter search does, and for the same reason: a
chunk id is not a capability, and neither is a document id. Both are handed to an
agent in a search result and can be replayed by whoever the agent is working for,
so they are tested against Postgres rather than a fake.
"""

import hashlib
from contextlib import contextmanager
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag_api import main as main_module
from opensql_autorag_api.outline_access import OutlineIdentity
from opensql_autorag_api.repository import Repository

DIMENSION = 384
TOKEN = "ol_api_developer_token"


class FakeResolver:
    """Reads one token as a member of col-platform, and nothing else."""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def resolve(self, token: str) -> OutlineIdentity:
        self.tokens.append(token)
        return OutlineIdentity(
            user_id="user-1",
            user_name="Dana",
            collection_ids=("col-platform",) if token == TOKEN else (),
        )


class Seeded:
    """One readable document and one that is not, each with two chunks."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository
        self.model_id = repository.resolve_embedding_model_id(
            provider="test-agent", model_name=f"probe-{uuid4()}", dimension=DIMENSION
        )
        self.documents: dict[str, UUID] = {}
        self.chunks: dict[str, UUID] = {}

    def add(self, name: str, collection_id: str) -> None:
        created = self.repository.create_document_version(
            title=name,
            source_type="md",
            source_path=f"/tmp/{name}.md",
            file_hash=hashlib.sha256(name.encode()).hexdigest(),
        )
        self.repository.upsert_document_source(
            document_id=created.document_id,
            source_system="outline",
            external_id=str(created.document_id),
            external_url=f"https://wiki.example.com/doc/{name}",
            external_updated_at=None,
            collection_id=collection_id,
            last_file_hash="hash",
        )
        for index in (0, 1):
            text = f"{name} section {index}"
            chunk_id = self.repository.insert_chunk_with_embedding(
                document_id=created.document_id,
                version_id=created.version_id,
                chunk=Chunk(
                    stable_key=f"{name}-{index}",
                    text=text,
                    content_hash=hashlib.sha256(text.encode()).hexdigest(),
                    chunk_index=index,
                    location=SourceLocation(heading_path=("Top",)),
                    token_estimate=3,
                ),
                embedding=[1.0] + [0.0] * (DIMENSION - 1),
                embedding_model_id=self.model_id,
            )
            self.chunks[f"{name}-{index}"] = chunk_id
        self.repository.complete_indexing(
            job_id=created.job_id,
            document_id=created.document_id,
            version_id=created.version_id,
            reused_count=1,
            embedded_count=2,
            retired_count=0,
            elapsed_ms=7,
        )
        self.documents[name] = created.document_id


@pytest.fixture
def seeded(db_connection):
    fixture = Seeded(Repository(db_connection))
    fixture.add("runbook", collection_id="col-platform")
    fixture.add("board-review", collection_id="col-secrets")
    return fixture


@pytest.fixture
def client(db_connection, monkeypatch):
    """The API wired to the rolled-back test connection and a fake Outline."""

    @contextmanager
    def shared_connection():
        yield db_connection

    monkeypatch.setattr(main_module, "get_connection", shared_connection)
    monkeypatch.setattr(main_module, "resolver", FakeResolver())
    return TestClient(main_module.app)


def as_developer(client: TestClient, path: str):
    return client.get(path, headers={"X-Outline-Token": TOKEN})


def test_chunk_context_returns_the_chunks_either_side(client, seeded):
    hit = seeded.chunks["runbook-0"]

    body = as_developer(client, f"/chunks/{hit}/context").json()

    assert [row["text"] for row in body["context"]] == [
        "runbook section 0",
        "runbook section 1",
    ]


def test_chunk_context_is_empty_for_a_document_the_caller_cannot_read(client, seeded):
    """The neighbouring text is the payload, so a chunk id must not fetch it."""
    hit = seeded.chunks["board-review-0"]

    body = as_developer(client, f"/chunks/{hit}/context").json()

    assert body["context"] == []


def test_chunk_context_without_a_token_reaches_no_wiki_document(client, seeded):
    hit = seeded.chunks["runbook-0"]

    body = client.get(f"/chunks/{hit}/context").json()

    assert body["context"] == []


def test_sync_status_reports_the_last_run(client, seeded):
    document = seeded.documents["runbook"]

    body = as_developer(client, f"/documents/{document}/sync-status").json()

    assert body["status"]["embedded_count"] == 2
    assert body["status"]["reused_count"] == 1


def test_sync_status_of_an_unreadable_document_is_indistinguishable_from_none(client, seeded):
    """Answering "exists but not for you" would confirm the id names something."""
    document = seeded.documents["board-review"]

    body = as_developer(client, f"/documents/{document}/sync-status").json()

    assert body["status"] is None


def test_sync_status_of_an_unknown_document_is_null(client, seeded):
    body = as_developer(client, f"/documents/{uuid4()}/sync-status").json()

    assert body["status"] is None


@pytest.mark.parametrize(
    "path",
    ["/chunks/not-a-uuid/context", "/documents/not-a-uuid/sync-status"],
)
def test_an_id_that_is_not_a_uuid_is_refused(client, path):
    assert as_developer(client, path).status_code == 422
