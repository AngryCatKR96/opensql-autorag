from __future__ import annotations

from pathlib import Path

from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision
from opensql_autorag.embeddings import EmbeddingProvider, HashEmbeddingProvider
from opensql_autorag_worker.extractors import extract_blocks


class CountingEmbeddingProvider:
    dimension = 384

    def __init__(self) -> None:
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return [0.0] * self.dimension


class IndexProcessor:
    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        chunker: SemanticChunker | None = None,
    ) -> None:
        self.chunker = chunker or SemanticChunker()
        self.delta_planner = DeltaPlanner()
        self.embedding_provider = embedding_provider or HashEmbeddingProvider(dimension=384)

    def preview_file(
        self,
        document_id: str,
        path: Path,
        previous_chunks: tuple[Chunk, ...] = (),
    ) -> dict[str, int]:
        blocks = extract_blocks(path)
        chunks = self.chunker.chunk(document_id, blocks)
        plan = self.delta_planner.plan(previous=previous_chunks, current=chunks)
        for item in plan.chunks:
            if item.decision == ChunkDecision.EMBED:
                self.embedding_provider.embed(item.chunk.text)
        return {
            "blocks": len(blocks),
            "chunks": len(chunks),
            "embedded": plan.embedded_count,
            "reused": plan.reused_count,
            "retired": plan.retired_count,
        }
