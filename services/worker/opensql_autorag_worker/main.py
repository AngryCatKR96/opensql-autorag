from __future__ import annotations

import time
from pathlib import Path
from time import perf_counter
from uuid import UUID

from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision, SourceLocation
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
from opensql_autorag_worker.extractors import extract_blocks
from opensql_autorag_worker.processor import IndexProcessor


def _row_to_chunk(row: dict) -> Chunk:
    heading_path = tuple(part.strip() for part in str(row["heading_path"]).split("/") if part.strip())
    return Chunk(
        stable_key=str(row["stable_key"]),
        text=str(row["text"]),
        content_hash=str(row["content_hash"]),
        chunk_index=int(row["chunk_index"]),
        location=SourceLocation(
            page_start=row["page_start"],
            page_end=row["page_end"],
            heading_path=heading_path,
        ),
        token_estimate=int(row["token_estimate"]),
    )


def process_next_job() -> bool:
    started = perf_counter()
    provider = HashEmbeddingProvider(dimension=384)
    processor = IndexProcessor(embedding_provider=provider)
    planner = DeltaPlanner()
    with get_connection() as connection:
        repo = Repository(connection)
        job = repo.claim_next_job()
        if job is None:
            return False
        job_id = UUID(str(job["id"]))
        document_id = UUID(str(job["document_id"]))
        version_id = UUID(str(job["version_id"]))
        try:
            source_path = Path(repo.get_version_source_path(version_id))
            blocks = extract_blocks(source_path)
            current_chunks = processor.chunker.chunk(str(document_id), blocks)
            previous_chunks = tuple(_row_to_chunk(row) for row in repo.load_active_chunks(document_id))
            plan = planner.plan(previous=previous_chunks, current=current_chunks)
            for item in plan.chunks:
                if item.decision == ChunkDecision.EMBED:
                    embedding = provider.embed(item.chunk.text)
                    repo.insert_chunk_with_embedding(document_id, version_id, item.chunk, embedding)
                elif item.decision == ChunkDecision.REUSE:
                    repo.insert_chunk_reusing_embedding(document_id, version_id, item.chunk)
            elapsed_ms = int((perf_counter() - started) * 1000)
            repo.complete_indexing(
                job_id=job_id,
                document_id=document_id,
                version_id=version_id,
                reused_count=plan.reused_count,
                embedded_count=plan.embedded_count,
                retired_count=plan.retired_count,
                elapsed_ms=elapsed_ms,
            )
            return True
        except Exception as exc:
            repo.mark_job_failed(job_id, str(exc))
            return True


def run_worker() -> None:
    while True:
        processed = process_next_job()
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    run_worker()
