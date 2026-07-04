from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class RetrievalResult:
    chunk_id: UUID
    document_id: UUID
    version_id: UUID
    text: str
    score: float
    heading_path: str
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class RetrievalQuery:
    query: str
    top_k: int = 5
