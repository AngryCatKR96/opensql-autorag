from opensql_autorag.chunking import SemanticChunker
from opensql_autorag.domain import SourceLocation, TextBlock


def test_chunker_preserves_heading_path_and_source_location():
    blocks = [
        TextBlock("OpenSQL overview", SourceLocation(1, 1, ("Intro",)), 0),
        TextBlock(
            "pgvector stores embeddings for semantic search.",
            SourceLocation(1, 1, ("Intro",)),
            1,
        ),
    ]

    chunks = SemanticChunker(target_tokens=20, overlap_tokens=4).chunk("doc-1", blocks)

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Intro",)
    assert chunks[0].location.page_start == 1
    assert "pgvector" in chunks[0].text


def test_chunker_does_not_carry_overlap_across_a_section_boundary():
    """A chunk must not open with words belonging to the previous heading.

    The overlap exists to keep context across a split inside one section. Carried
    across a boundary it mislabels the text — the chunk's heading path would name
    the new section while its first words come from the old one — and it couples
    the two sections, so editing one re-embeds both.
    """
    blocks = [
        TextBlock("alpha beta gamma delta", SourceLocation(heading_path=("Doc", "First")), 0),
        TextBlock("epsilon zeta", SourceLocation(heading_path=("Doc", "Second")), 1),
    ]

    chunks = SemanticChunker(target_tokens=20, overlap_tokens=3).chunk("doc-1", blocks)

    assert [chunk.location.heading_path for chunk in chunks] == [
        ("Doc", "First"),
        ("Doc", "Second"),
    ]
    assert chunks[1].text == "epsilon zeta"


def test_chunker_splits_large_sections_deterministically():
    text = " ".join(f"token{i}" for i in range(45))
    blocks = [TextBlock(text, SourceLocation(2, 2, ("Long",)), 0)]

    chunks = SemanticChunker(target_tokens=15, overlap_tokens=3).chunk("doc-1", blocks)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2, 3]
    assert chunks[0].stable_key != chunks[1].stable_key
    assert chunks[1].text.startswith("token12")


def test_heading_with_no_body_joins_the_section_below_it():
    """A document title followed straight by its first subsection.

    On its own it would be a two or three word chunk competing for a result
    slot, so its words belong to the section beneath it.
    """
    blocks = [
        TextBlock("Runbook", SourceLocation(heading_path=("Runbook",)), 0, is_heading=True),
        TextBlock("Scope", SourceLocation(heading_path=("Runbook", "Scope")), 1, is_heading=True),
        TextBlock("Covers retrieval.", SourceLocation(heading_path=("Runbook", "Scope")), 2),
    ]

    chunks = SemanticChunker(target_tokens=50, overlap_tokens=5).chunk("doc-1", blocks)

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Runbook", "Scope")
    assert chunks[0].text == "Runbook Scope Covers retrieval."


def test_a_section_with_body_still_gets_its_own_chunk():
    """The merge above must not swallow a section that has content."""
    blocks = [
        TextBlock("Runbook", SourceLocation(heading_path=("Runbook",)), 0, is_heading=True),
        TextBlock("Read this first.", SourceLocation(heading_path=("Runbook",)), 1),
        TextBlock("Scope", SourceLocation(heading_path=("Runbook", "Scope")), 2, is_heading=True),
        TextBlock("Covers retrieval.", SourceLocation(heading_path=("Runbook", "Scope")), 3),
    ]

    chunks = SemanticChunker(target_tokens=50, overlap_tokens=5).chunk("doc-1", blocks)

    assert [chunk.location.heading_path for chunk in chunks] == [
        ("Runbook",),
        ("Runbook", "Scope"),
    ]
