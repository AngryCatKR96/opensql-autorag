from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from psycopg import Connection


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
