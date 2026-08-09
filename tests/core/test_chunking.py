import pytest
from opensql_autorag.chunking import SemanticChunker, common_heading_path
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

    # min_tokens=1 keeps the thin-section merge out of the way, so this is only
    # about what the overlap does at a boundary.
    chunks = SemanticChunker(target_tokens=20, overlap_tokens=3, min_tokens=1).chunk(
        "doc-1", blocks
    )

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


def test_a_section_with_enough_body_still_gets_its_own_chunk():
    """The merges must not swallow a section that carries its own weight."""
    body = " ".join(f"word{i}" for i in range(12))
    blocks = [
        TextBlock("Runbook", SourceLocation(heading_path=("Runbook",)), 0, is_heading=True),
        TextBlock(body, SourceLocation(heading_path=("Runbook",)), 1),
        TextBlock("Scope", SourceLocation(heading_path=("Runbook", "Scope")), 2, is_heading=True),
        TextBlock(body, SourceLocation(heading_path=("Runbook", "Scope")), 3),
    ]

    chunks = SemanticChunker(target_tokens=50, overlap_tokens=5, min_tokens=10).chunk(
        "doc-1", blocks
    )

    assert [chunk.location.heading_path for chunk in chunks] == [
        ("Runbook",),
        ("Runbook", "Scope"),
    ]


def test_a_section_too_thin_to_stand_alone_joins_the_next_one():
    """An eight word section is not a retrieval unit.

    Splitting by heading is what makes retrieval and delta sync work, but it also
    turns a one-line section into a chunk carrying too little for a query to tell
    it apart from any other.
    """
    blocks = [
        TextBlock(
            "Travel", SourceLocation(heading_path=("Expenses", "Travel")), 0, is_heading=True
        ),
        TextBlock(
            "Economy under six hours.", SourceLocation(heading_path=("Expenses", "Travel")), 1
        ),
        TextBlock(
            "Equipment", SourceLocation(heading_path=("Expenses", "Equipment")), 2, is_heading=True
        ),
        TextBlock(
            "A laptop every three years.", SourceLocation(heading_path=("Expenses", "Equipment")), 3
        ),
    ]

    chunks = SemanticChunker(target_tokens=50, overlap_tokens=5, min_tokens=12).chunk(
        "doc-1", blocks
    )

    assert len(chunks) == 1
    # Siblings, so neither may claim the chunk: it takes what they share.
    assert chunks[0].location.heading_path == ("Expenses",)
    assert "Economy" in chunks[0].text and "laptop" in chunks[0].text


def test_a_thin_section_is_left_alone_rather_than_making_an_oversized_chunk():
    """Merging is a repair, not an obligation.

    A one line section followed by a long one should not produce a chunk past
    the target size just to avoid a small one.
    """
    blocks = [
        TextBlock("Scope", SourceLocation(heading_path=("Doc", "Scope")), 0, is_heading=True),
        TextBlock("Just this.", SourceLocation(heading_path=("Doc", "Scope")), 1),
        TextBlock("Detail", SourceLocation(heading_path=("Doc", "Detail")), 2, is_heading=True),
        TextBlock(
            " ".join(f"word{i}" for i in range(30)),
            SourceLocation(heading_path=("Doc", "Detail")),
            3,
        ),
    ]

    chunks = SemanticChunker(target_tokens=20, overlap_tokens=0, min_tokens=10).chunk(
        "doc-1", blocks
    )

    assert chunks[0].location.heading_path == ("Doc", "Scope")
    assert all(chunk.token_estimate <= 20 for chunk in chunks)


def test_common_path_is_the_deepest_heading_both_sit_under():
    assert common_heading_path(("A", "B"), ("A", "C")) == ("A",)
    assert common_heading_path(("Runbook",), ("Runbook", "Scope")) == ("Runbook",)
    assert common_heading_path(("A", "B"), ("X", "Y")) == ()


def test_repeated_merges_do_not_re_specialise_the_label():
    """A chunk spanning three sections must not be named after the last one.

    Preferring the more specific of two paths at each step lets the final merge
    re-narrow a label that had already widened, titling a chunk after the one
    section it is least about.
    """

    def section(name: str, words: int) -> list[TextBlock]:
        path = ("Expenses", name)
        return [
            TextBlock(name, SourceLocation(heading_path=path), 0, is_heading=True),
            TextBlock(
                " ".join(f"w{i}" for i in range(words)),
                SourceLocation(heading_path=path),
                1,
            ),
        ]

    blocks = section("Travel", 5) + section("Equipment", 5) + section("Receipts", 5)

    chunks = SemanticChunker(target_tokens=100, overlap_tokens=0, min_tokens=40).chunk(
        "doc-1", blocks
    )

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Expenses",)


def test_a_thin_last_section_folds_backwards():
    """The final section has nothing after it to join, so it joins what precedes it."""
    body = " ".join(f"word{i}" for i in range(14))
    blocks = [
        TextBlock("Body", SourceLocation(heading_path=("Doc", "Body")), 0, is_heading=True),
        TextBlock(body, SourceLocation(heading_path=("Doc", "Body")), 1),
        TextBlock("Note", SourceLocation(heading_path=("Doc", "Note")), 2, is_heading=True),
        TextBlock("Two words.", SourceLocation(heading_path=("Doc", "Note")), 3),
    ]

    chunks = SemanticChunker(target_tokens=50, overlap_tokens=5, min_tokens=10).chunk(
        "doc-1", blocks
    )

    assert len(chunks) == 1
    assert chunks[0].location.heading_path == ("Doc",)
    assert "Two words." in chunks[0].text


def test_min_tokens_must_leave_room_under_the_target():
    with pytest.raises(ValueError, match="min_tokens"):
        SemanticChunker(target_tokens=20, overlap_tokens=3, min_tokens=20)
