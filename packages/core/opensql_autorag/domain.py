from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ChunkDecision(StrEnum):
    REUSE = "reuse"
    EMBED = "embed"
    RETIRE = "retire"


@dataclass(frozen=True)
class SourceLocation:
    page_start: int | None = None
    page_end: int | None = None
    heading_path: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TextBlock:
    text: str
    location: SourceLocation
    block_index: int


@dataclass(frozen=True)
class Chunk:
    stable_key: str
    text: str
    content_hash: str
    chunk_index: int
    location: SourceLocation
    token_estimate: int


@dataclass(frozen=True)
class PlannedChunk:
    chunk: Chunk
    decision: ChunkDecision
    previous_stable_key: str | None = None


@dataclass(frozen=True)
class DeltaPlan:
    chunks: tuple[PlannedChunk, ...]

    @property
    def reused_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.REUSE)

    @property
    def embedded_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.EMBED)

    @property
    def retired_count(self) -> int:
        return sum(1 for item in self.chunks if item.decision == ChunkDecision.RETIRE)
