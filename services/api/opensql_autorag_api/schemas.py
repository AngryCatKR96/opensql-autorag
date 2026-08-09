from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    job_id: UUID


class DocumentSummary(BaseModel):
    id: UUID
    title: str
    source_type: str
    current_version_id: UUID | None
    active_chunk_count: int
    # Set when the document was removed at its source; its chunks are retained
    # but none of them are searchable.
    retired_at: datetime | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class SearchResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    text: str
    score: float
    heading_path: str
    page_start: int | None
    page_end: int | None
