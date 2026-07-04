from __future__ import annotations

from pathlib import Path

from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.embeddings import HashEmbeddingProvider
from opensql_autorag_worker.extractors import extract_blocks


class IndexProcessor:
    def __init__(self) -> None:
        self.chunker = SemanticChunker()
        self.delta_planner = DeltaPlanner()
        self.embedding_provider = HashEmbeddingProvider(dimension=384)

    def preview_file(self, document_id: str, path: Path) -> dict[str, int]:
        blocks = extract_blocks(path)
        chunks = self.chunker.chunk(document_id, blocks)
        plan = self.delta_planner.plan(previous=(), current=chunks)
        for item in plan.chunks:
            if item.decision == "embed":
                self.embedding_provider.embed(item.chunk.text)
        return {
            "blocks": len(blocks),
            "chunks": len(chunks),
            "embedded": plan.embedded_count,
            "reused": plan.reused_count,
            "retired": plan.retired_count,
        }
