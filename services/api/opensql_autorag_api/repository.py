from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from opensql_autorag.domain import Chunk
from psycopg import Connection


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"


@dataclass(frozen=True)
class CreatedVersion:
    document_id: UUID
    version_id: UUID
    job_id: UUID


@dataclass(frozen=True)
class SearchScope:
    """Which documents one search is allowed to return.

    Documents synced from an external system are only reachable through a
    collection the caller can read there; a document whose collection is
    unknown is unreachable, never public by default. Documents uploaded
    straight into AutoRAG have no external permissions to honour, so they are
    governed by `include_local_documents` instead.
    """

    allowed_collection_ids: tuple[str, ...] = ()
    include_local_documents: bool = True

    @classmethod
    def local_only(cls) -> SearchScope:
        """The scope of a caller who proved no access to the external system."""
        return cls(allowed_collection_ids=(), include_local_documents=True)


class Repository:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def create_document_version(
        self,
        title: str,
        source_type: str,
        source_path: str,
        file_hash: str,
        document_id: UUID | None = None,
    ) -> CreatedVersion:
        doc_id = document_id or uuid4()
        version_id = uuid4()
        job_id = uuid4()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents (id, title, source_type)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                (doc_id, title, source_type),
            )
            cursor.execute(
                """
                INSERT INTO document_versions (id, document_id, file_hash, status, source_path)
                VALUES (%s, %s, %s, 'pending', %s)
                """,
                (version_id, doc_id, file_hash, source_path),
            )
            cursor.execute(
                """
                INSERT INTO index_jobs (id, document_id, version_id, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (job_id, doc_id, version_id),
            )
        return CreatedVersion(document_id=doc_id, version_id=version_id, job_id=job_id)

    def upsert_document_source(
        self,
        document_id: UUID,
        source_system: str,
        external_id: str,
        external_url: str | None,
        external_updated_at: str | None,
        collection_id: str | None,
        last_file_hash: str | None,
    ) -> None:
        """Record where a document came from, so later syncs can skip unchanged ones."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_sources (
                    document_id, source_system, external_id, external_url,
                    external_updated_at, collection_id, last_file_hash, synced_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (document_id) DO UPDATE SET
                    source_system = excluded.source_system,
                    external_id = excluded.external_id,
                    external_url = excluded.external_url,
                    external_updated_at = excluded.external_updated_at,
                    collection_id = excluded.collection_id,
                    last_file_hash = excluded.last_file_hash,
                    synced_at = now()
                """,
                (
                    document_id,
                    source_system,
                    external_id,
                    external_url,
                    external_updated_at,
                    collection_id,
                    last_file_hash,
                ),
            )

    def synced_document_ids(self, source_system: str, collection_ids: tuple[str, ...]) -> set[UUID]:
        """Documents this source has synced, optionally within some collections.

        Lets a full re-sync of a scope find what has since disappeared from it.
        """
        with self.connection.cursor() as cursor:
            if collection_ids:
                cursor.execute(
                    """
                    SELECT d.id
                    FROM documents d
                    JOIN document_sources s ON s.document_id = d.id
                    WHERE s.source_system = %s
                      AND s.collection_id = ANY(%s::text[])
                      AND d.retired_at IS NULL
                    """,
                    (source_system, list(collection_ids)),
                )
            else:
                cursor.execute(
                    """
                    SELECT d.id
                    FROM documents d
                    JOIN document_sources s ON s.document_id = d.id
                    WHERE s.source_system = %s AND d.retired_at IS NULL
                    """,
                    (source_system,),
                )
            return {row["id"] for row in cursor.fetchall()}

    def get_document_source(self, document_id: UUID) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT document_id, source_system, external_id, external_url,
                       external_updated_at, collection_id, last_file_hash, synced_at
                FROM document_sources
                WHERE document_id = %s
                """,
                (document_id,),
            )
            return cursor.fetchone()

    def list_documents(self, scope: SearchScope) -> list[dict]:
        """Documents the caller may see. A title is content, so it is scoped too."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.id, d.title, d.source_type, d.current_version_id,
                       d.created_at, d.updated_at, d.retired_at,
                       s.collection_id AS source_collection_id,
                       COUNT(c.id) FILTER (WHERE c.active) AS active_chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.id
                LEFT JOIN document_sources s ON s.document_id = d.id
                WHERE (s.document_id IS NULL AND %s)
                   OR s.collection_id = ANY(%s::text[])
                GROUP BY d.id, s.collection_id
                ORDER BY d.updated_at DESC
                """,
                (scope.include_local_documents, list(scope.allowed_collection_ids)),
            )
            return list(cursor.fetchall())

    def document_in_scope(self, document_id: UUID, scope: SearchScope) -> bool:
        """Whether the caller may read anything belonging to this document."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM documents d
                LEFT JOIN document_sources s ON s.document_id = d.id
                WHERE d.id = %s
                  AND (
                        (s.document_id IS NULL AND %s)
                     OR s.collection_id = ANY(%s::text[])
                  )
                """,
                (document_id, scope.include_local_documents, list(scope.allowed_collection_ids)),
            )
            return cursor.fetchone() is not None

    def claim_next_job(self) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'running', attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id
                    FROM index_jobs
                    WHERE status = 'pending'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, document_id, version_id
                """
            )
            return cursor.fetchone()

    def get_version_source_path(self, version_id: UUID) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT source_path FROM document_versions WHERE id = %s",
                (version_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"version not found: {version_id}")
            return str(row["source_path"])

    def mark_job_failed(self, job_id: UUID, message: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'failed', error_message = %s, updated_at = now()
                WHERE id = %s
                """,
                (message, job_id),
            )

    def load_active_chunks(self, document_id: UUID) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, stable_key, text, content_hash, chunk_index, heading_path,
                       page_start, page_end, token_estimate
                FROM document_chunks
                WHERE document_id = %s AND active = TRUE
                ORDER BY chunk_index
                """,
                (document_id,),
            )
            return list(cursor.fetchall())

    def resolve_embedding_model_id(self, provider: str, model_name: str, dimension: int) -> int:
        """Return the embedding_models row for this provider, registering it if new.

        The dimension of an existing row is never rewritten: a model that changes
        dimension is a different model, and its stored vectors are not comparable.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO embedding_models (provider, model_name, dimension)
                VALUES (%s, %s, %s)
                ON CONFLICT (provider, model_name) DO UPDATE SET enabled = TRUE
                RETURNING id, dimension
                """,
                (provider, model_name, dimension),
            )
            row = cursor.fetchone()
        if row["dimension"] != dimension:
            raise ValueError(
                f"embedding model {provider}/{model_name} is registered with "
                f"dimension {row['dimension']}, but the provider reports {dimension}"
            )
        return int(row["id"])

    def embedding_column_dimension(self) -> int:
        """The dimension fixed in the chunk_embeddings.embedding column type."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT atttypmod AS dimension
                FROM pg_attribute
                WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'
                """
            )
            row = cursor.fetchone()
        if row is None:
            raise ValueError("chunk_embeddings.embedding column not found")
        return int(row["dimension"])

    def has_reusable_embedding(
        self,
        document_id: UUID,
        content_hash: str,
        embedding_model_id: int,
    ) -> bool:
        """Whether a chunk with this content was already embedded by this model."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                  AND c.content_hash = %s
                  AND e.embedding_model_id = %s
                LIMIT 1
                """,
                (document_id, content_hash, embedding_model_id),
            )
            return cursor.fetchone() is not None

    def insert_chunk_with_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding: list[float],
        embedding_model_id: int,
    ) -> UUID:
        chunk_id = uuid4()
        heading_path = " / ".join(chunk.location.heading_path)
        vector_literal = _vector_literal(embedding)
        vector_hash = hashlib.sha256(vector_literal.encode("utf-8")).hexdigest()
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, version_id, stable_key, chunk_index, text,
                    content_hash, heading_path, page_start, page_end, token_estimate, active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    chunk_id,
                    document_id,
                    version_id,
                    chunk.stable_key,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.content_hash,
                    heading_path,
                    chunk.location.page_start,
                    chunk.location.page_end,
                    chunk.token_estimate,
                ),
            )
            cursor.execute(
                """
                INSERT INTO chunk_embeddings (
                    chunk_id, embedding_model_id, embedding, vector_hash
                )
                VALUES (%s, %s, %s::vector, %s)
                """,
                (chunk_id, embedding_model_id, vector_literal, vector_hash),
            )
        return chunk_id

    def search_chunks(
        self,
        query_embedding: list[float],
        top_k: int,
        embedding_model_id: int,
        scope: SearchScope,
    ) -> list[dict]:
        """Nearest active chunks the caller is allowed to see.

        Distances between vectors of different models are meaningless, so a query
        never compares against embeddings produced by another model.

        `scope` is applied inside the query rather than to its results, so a
        document out of scope cannot consume one of the `top_k` slots. The
        collection comparison excludes a NULL `collection_id`, which is what
        makes an externally sourced document with no known collection
        unreachable.
        """
        vector_literal = _vector_literal(query_embedding)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.version_id, c.text,
                       c.heading_path, c.page_start, c.page_end,
                       d.title AS document_title,
                       s.source_system, s.external_url AS source_url,
                       s.external_updated_at AS source_updated_at,
                       s.collection_id AS source_collection_id,
                       1 - (e.embedding <=> %s::vector) AS score
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                JOIN documents d ON d.id = c.document_id
                LEFT JOIN document_sources s ON s.document_id = c.document_id
                WHERE c.active = TRUE
                  AND e.embedding_model_id = %s
                  AND (
                        (s.document_id IS NULL AND %s)
                     OR s.collection_id = ANY(%s::text[])
                  )
                ORDER BY e.embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    vector_literal,
                    embedding_model_id,
                    scope.include_local_documents,
                    list(scope.allowed_collection_ids),
                    vector_literal,
                    top_k,
                ),
            )
            return list(cursor.fetchall())

    def deactivate_document(self, document_id: UUID) -> int:
        """Retire a document: its chunks stop being searchable.

        Used when a document is removed at the source. `retired_at` is what makes
        it stick: an indexing job queued before the removal would otherwise
        reactivate the chunks when it completes, so pending jobs are cancelled
        and `complete_indexing` honours the flag as well.

        Returns how many chunks stopped being active, which is 0 both for a
        document AutoRAG does not have and for one with nothing active left.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE documents SET retired_at = now(), updated_at = now() WHERE id = %s",
                (document_id,),
            )
            if cursor.rowcount == 0:
                return 0
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'cancelled', updated_at = now()
                WHERE document_id = %s AND status = 'pending'
                """,
                (document_id,),
            )
            cursor.execute(
                "UPDATE document_chunks SET active = FALSE WHERE document_id = %s AND active",
                (document_id,),
            )
            return cursor.rowcount

    def reactivate_document(self, document_id: UUID) -> bool:
        """Clear the retired flag so a re-index can make the document searchable.

        Chunks stay inactive until an indexing job completes, because the body may
        have changed while the document was away.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE documents
                SET retired_at = NULL, updated_at = now()
                WHERE id = %s AND retired_at IS NOT NULL
                """,
                (document_id,),
            )
            return cursor.rowcount > 0

    def is_retired(self, document_id: UUID) -> bool:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT retired_at IS NOT NULL AS retired FROM documents WHERE id = %s",
                (document_id,),
            )
            row = cursor.fetchone()
        return bool(row and row["retired"])

    def get_chunk_context(self, chunk_id: UUID, scope: SearchScope) -> list[dict]:
        """The chunks either side of one hit.

        Scoped like search: a chunk id is not a capability, so a caller who may
        not read the document gets nothing rather than its neighbouring text.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT c.id AS chunk_id, c.document_id, c.version_id, c.text,
                       c.heading_path, c.page_start, c.page_end, c.chunk_index
                FROM document_chunks c
                LEFT JOIN document_sources s ON s.document_id = c.document_id
                WHERE (
                        (s.document_id IS NULL AND %s)
                     OR s.collection_id = ANY(%s::text[])
                      )
                AND c.version_id = (
                    SELECT version_id FROM document_chunks WHERE id = %s
                )
                AND c.chunk_index BETWEEN (
                    SELECT chunk_index - 1 FROM document_chunks WHERE id = %s
                ) AND (
                    SELECT chunk_index + 1 FROM document_chunks WHERE id = %s
                )
                ORDER BY c.chunk_index
                """,
                (
                    scope.include_local_documents,
                    list(scope.allowed_collection_ids),
                    chunk_id,
                    chunk_id,
                    chunk_id,
                ),
            )
            return list(cursor.fetchall())

    def latest_sync_status(self, document_id: UUID) -> dict | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, document_id, version_id, reused_count, embedded_count,
                       retired_count, failed_count, elapsed_ms, created_at
                FROM sync_runs
                WHERE document_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (document_id,),
            )
            return cursor.fetchone()

    def insert_chunk_reusing_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding_model_id: int,
    ) -> UUID:
        chunk_id = uuid4()
        heading_path = " / ".join(chunk.location.heading_path)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO document_chunks (
                    id, document_id, version_id, stable_key, chunk_index, text,
                    content_hash, heading_path, page_start, page_end, token_estimate, active
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
                """,
                (
                    chunk_id,
                    document_id,
                    version_id,
                    chunk.stable_key,
                    chunk.chunk_index,
                    chunk.text,
                    chunk.content_hash,
                    heading_path,
                    chunk.location.page_start,
                    chunk.location.page_end,
                    chunk.token_estimate,
                ),
            )
            cursor.execute(
                """
                INSERT INTO chunk_embeddings (
                    chunk_id, embedding_model_id, embedding, vector_hash
                )
                SELECT %s, e.embedding_model_id, e.embedding, e.vector_hash
                FROM chunk_embeddings e
                JOIN document_chunks c ON c.id = e.chunk_id
                WHERE c.document_id = %s
                  AND c.content_hash = %s
                  AND e.embedding_model_id = %s
                ORDER BY c.created_at DESC
                LIMIT 1
                """,
                (chunk_id, document_id, chunk.content_hash, embedding_model_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"previous embedding not found for chunk hash: {chunk.content_hash}"
                )
        return chunk_id

    def complete_indexing(
        self,
        job_id: UUID,
        document_id: UUID,
        version_id: UUID,
        reused_count: int,
        embedded_count: int,
        retired_count: int,
        elapsed_ms: int,
    ) -> None:
        sync_run_id = uuid4()
        with self.connection.cursor() as cursor:
            # A document retired while this job was queued stays unsearchable:
            # activating its chunks here would undo the removal.
            cursor.execute(
                """
                UPDATE document_chunks c
                SET active = (c.version_id = %s AND d.retired_at IS NULL)
                FROM documents d
                WHERE d.id = c.document_id AND c.document_id = %s
                """,
                (version_id, document_id),
            )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'indexed',
                    extracted_text_hash = COALESCE(extracted_text_hash, file_hash)
                WHERE id = %s
                """,
                (version_id,),
            )
            cursor.execute(
                """
                UPDATE documents
                SET current_version_id = %s, updated_at = now()
                WHERE id = %s
                """,
                (version_id, document_id),
            )
            cursor.execute(
                """
                UPDATE index_jobs
                SET status = 'succeeded', updated_at = now()
                WHERE id = %s
                """,
                (job_id,),
            )
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    id, document_id, version_id, reused_count, embedded_count,
                    retired_count, elapsed_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    sync_run_id,
                    document_id,
                    version_id,
                    reused_count,
                    embedded_count,
                    retired_count,
                    elapsed_ms,
                ),
            )
