from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, UploadFile

from opensql_autorag_api.db import get_connection
from opensql_autorag_api.repository import Repository
from opensql_autorag_api.schemas import DocumentSummary, DocumentUploadResponse, SearchRequest
from opensql_autorag_api.settings import settings

app = FastAPI(title="OpenSQL AutoRAG Sync")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/documents", response_model=list[DocumentSummary])
def list_documents() -> list[dict]:
    with get_connection() as connection:
        return Repository(connection).list_documents()


async def _store_document_upload(
    file: UploadFile,
    document_id: UUID | None = None,
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    suffix = Path(file.filename).suffix.lower().lstrip(".")
    if suffix not in {"pdf", "docx", "md", "txt"}:
        raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}")

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    file_hash = hashlib.sha256(content).hexdigest()
    source_path = settings.storage_dir / f"{file_hash}.{suffix}"
    source_path.write_bytes(content)

    with get_connection() as connection:
        created = Repository(connection).create_document_version(
            title=file.filename,
            source_type=suffix,
            source_path=str(source_path),
            file_hash=file_hash,
            document_id=document_id,
        )

    return DocumentUploadResponse(
        document_id=created.document_id,
        version_id=created.version_id,
        job_id=created.job_id,
    )


@app.post("/documents", response_model=DocumentUploadResponse)
async def upload_document(file: Annotated[UploadFile, File()]) -> DocumentUploadResponse:
    return await _store_document_upload(file)


@app.post("/documents/{document_id}/versions", response_model=DocumentUploadResponse)
async def upload_document_version(
    document_id: UUID,
    file: Annotated[UploadFile, File()],
) -> DocumentUploadResponse:
    return await _store_document_upload(file, document_id=document_id)


@app.post("/search")
def search_documents(request: SearchRequest) -> dict:
    from opensql_autorag.embeddings import HashEmbeddingProvider

    provider = HashEmbeddingProvider(dimension=settings.embedding_dimension)
    query_embedding = provider.embed(request.query)
    with get_connection() as connection:
        rows = Repository(connection).search_chunks(query_embedding, request.top_k)
    return {"query": request.query, "top_k": request.top_k, "results": rows}
