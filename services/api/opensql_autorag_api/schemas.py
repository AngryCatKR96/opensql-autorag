from __future__ import annotations

from datetime import datetime
from typing import Literal
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
    # What the last indexing run did: how many chunks kept their existing vector
    # and how many had to be embedded again. Null before a document has ever
    # been indexed. Reuse of zero on every run means delta sync is not working.
    last_reused_count: int | None = None
    last_embedded_count: int | None = None
    # Set when the document was removed at its source; its chunks are retained
    # but none of them are searchable.
    retired_at: datetime | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    # None follows AUTORAG_SEARCH_MODE. `hybrid` fuses both arms; `vector` and
    # `keyword` are useful for seeing what each one contributes on its own.
    mode: Literal["hybrid", "vector", "keyword"] | None = None


class SearchResult(BaseModel):
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    text: str
    score: float
    heading_path: str
    page_start: int | None
    page_end: int | None
    # Present under `hybrid`, where `score` is the fused rank and these are what
    # each arm said on its own. Null means that arm did not return this chunk.
    vector_score: float | None = None
    keyword_score: float | None = None
    matched_by: list[str] = Field(default_factory=list)
