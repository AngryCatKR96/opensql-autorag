from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg import Connection

from opensql_autorag.domain import Chunk


@dataclass(frozen=True)
class CreatedVersion:
    document_id: UUID
    version_id: UUID
    job_id: UUID


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

    def list_documents(self) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.id, d.title, d.source_type, d.current_version_id, d.created_at, d.updated_at,
                       COUNT(c.id) FILTER (WHERE c.active) AS active_chunk_count
                FROM documents d
                LEFT JOIN document_chunks c ON c.document_id = d.id
                GROUP BY d.id
                ORDER BY d.updated_at DESC
                """
            )
            return list(cursor.fetchall())

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

    def insert_chunk_with_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding: list[float],
        embedding_model_id: int = 1,
    ) -> UUID:
        chunk_id = uuid4()
        heading_path = " / ".join(chunk.location.heading_path)
        vector_literal = "[" + ",".join(f"{value:.8f}" for value in embedding) + "]"
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

    def insert_chunk_reusing_embedding(
        self,
        document_id: UUID,
        version_id: UUID,
        chunk: Chunk,
        embedding_model_id: int = 1,
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
                raise ValueError(f"previous embedding not found for chunk hash: {chunk.content_hash}")
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
            cursor.execute(
                "UPDATE document_chunks SET active = (version_id = %s) WHERE document_id = %s",
                (version_id, document_id),
            )
            cursor.execute(
                """
                UPDATE document_versions
                SET status = 'indexed', extracted_text_hash = COALESCE(extracted_text_hash, file_hash)
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
