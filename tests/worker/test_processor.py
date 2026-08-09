from pathlib import Path

from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.domain import Chunk, SourceLocation
from opensql_autorag.hash_utils import content_hash, stable_key
from opensql_autorag_worker.processor import CountingEmbeddingProvider, IndexProcessor


def make_previous_chunk(index: int, text: str) -> Chunk:
    location = SourceLocation(heading_path=())
    return Chunk(
        stable_key=stable_key("doc-1", location.heading_path, index, text),
        text=text,
        content_hash=content_hash(text),
        chunk_index=index,
        location=location,
        token_estimate=len(text.split()),
    )


def test_processor_embeds_only_changed_chunks(tmp_path: Path):
    path = tmp_path / "guide.txt"
    path.write_text("same content\nnew content", encoding="utf-8")
    provider = CountingEmbeddingProvider()
    processor = IndexProcessor(
        embedding_provider=provider,
        chunker=SemanticChunker(target_tokens=2, overlap_tokens=0),
    )

    summary = processor.preview_file(
        document_id="doc-1",
        path=path,
        previous_chunks=(make_previous_chunk(0, "same content"),),
    )

    assert summary["reused"] == 1
    assert summary["embedded"] == 1
    assert provider.calls == ["new content"]


def test_chunks_are_embedded_as_passages(tmp_path: Path):
    """Indexed content is never a question.

    Worth pinning: the role used to be inferred from length, so a short document
    was embedded as a query and compared against real queries on a different
    footing than a long one.
    """
    path = tmp_path / "doc.md"
    path.write_text("# Title\n\nfresh content that has to be embedded\n", encoding="utf-8")
    provider = CountingEmbeddingProvider()
    processor = IndexProcessor(embedding_provider=provider)

    processor.preview_file(document_id="doc-1", path=path, previous_chunks=())

    assert provider.roles, "nothing was embedded, so the role was never exercised"
    assert set(provider.roles) == {"passage"}
