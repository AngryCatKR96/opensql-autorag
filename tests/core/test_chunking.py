from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.domain import SourceLocation, TextBlock


def test_chunker_preserves_heading_path_and_source_location():
    blocks = [
        TextBlock("OpenSQL overview", SourceLocation(1, 1, ("Intro",)), 0),
        TextBlock("pgvector stores embeddings for semantic search.", SourceLocation(1, 1, ("Intro",)), 1),
    ]

    chunks = SemanticChunker(target_tokens=20, overlap_tokens=4).chunk("doc-1", blocks)

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Intro",)
    assert chunks[0].location.page_start == 1
    assert "pgvector" in chunks[0].text


def test_chunker_splits_large_sections_deterministically():
    text = " ".join(f"token{i}" for i in range(45))
    blocks = [TextBlock(text, SourceLocation(2, 2, ("Long",)), 0)]

    chunks = SemanticChunker(target_tokens=15, overlap_tokens=3).chunk("doc-1", blocks)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert chunks[0].stable_key != chunks[1].stable_key
    assert chunks[1].text.startswith("token12")
