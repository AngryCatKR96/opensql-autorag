"""Turn a source file into blocks carrying the heading each one sits under.

The heading path is what makes the chunker split a document by section: it
starts a new chunk wherever the path changes. Without one a file is an
undifferentiated run of lines that only ever splits on length, which costs the
section context in search results and costs delta sync most of its reuse --
edit one paragraph of a document held in a single chunk and the whole document
is re-embedded.

How much structure is available differs by format, and each extractor reads the
best signal its format actually carries rather than guessing:

| Format   | Heading signal                | Granularity     |
|----------|-------------------------------|-----------------|
| markdown | ATX and setext headings       | line            |
| docx     | Heading paragraph styles      | paragraph       |
| pdf      | Bookmarks, when present       | page            |
| txt      | none                          | whole document  |
"""

from __future__ import annotations

import re
from pathlib import Path

from opensql_autorag.domain import SourceLocation, TextBlock

# An ATX heading: one to six hashes, the text, and an optional closing run of
# hashes. Outline serialises every heading this way.
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

# The underline of a setext heading, which makes the line above it a heading.
_SETEXT_UNDERLINE = re.compile(r"^(=+|-+)$")

# A fenced code block opener or closer.
_CODE_FENCE = re.compile(r"^(`{3,}|~{3,})")

# A Word heading style. Matched against the style id as well as the name,
# because the name is localised by the authoring install while the id usually
# is not.
_WORD_HEADING = re.compile(r"^(heading|title)\s*(\d*)$", re.IGNORECASE)


def extract_blocks(path: Path) -> tuple[TextBlock, ...]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".md", ".markdown"}:
        return _extract_markdown(path)
    if suffix == ".txt":
        return _extract_plain_text(path)
    raise ValueError(f"unsupported file type: {suffix}")


def _push(headings: list[str], level: int, text: str) -> None:
    """Apply a heading at `level`, replacing everything from that level down.

    A level 2 heading after `A / X` yields `A / B`, not `A / X / B`. A skipped
    level keeps what is there rather than inventing a placeholder.
    """
    del headings[level - 1 :]
    headings.append(text)


def _extract_plain_text(path: Path) -> tuple[TextBlock, ...]:
    """Lines with no heading path: a .txt file has no structure to read.

    Nothing is inferred from its content, because a leading `#` in a text file
    is as likely to be a comment or a shell prompt as a title.
    """
    blocks: list[TextBlock] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if text:
            blocks.append(TextBlock(text=text, location=SourceLocation(), block_index=index))
    return tuple(blocks)


def _extract_markdown(path: Path) -> tuple[TextBlock, ...]:
    """Markdown, split by its headings.

    This carries the most weight of the four: Outline serves every wiki page as
    markdown, so the entire wiki path arrives here.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[TextBlock] = []
    headings: list[str] = []
    fence = ""
    skip_next = False

    for index, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue

        text = line.strip()
        if not text:
            continue

        is_heading = False
        opening = _CODE_FENCE.match(text)
        if fence:
            # Inside a code block. A `#` here starts a shell comment, not a
            # section, and a runbook is mostly shell.
            if opening and text.startswith(fence):
                fence = ""
                continue
        elif opening:
            fence = opening.group(1)
            continue
        else:
            atx = _ATX_HEADING.match(text)
            if atx:
                is_heading = True
                text = atx.group(2)
                _push(headings, len(atx.group(1)), text)
            else:
                # A setext heading is only recognisable from the line beneath
                # it: `Title` over `===` is a heading, the same line over
                # nothing is a paragraph.
                following = lines[index + 1].strip() if index + 1 < len(lines) else ""
                underline = _SETEXT_UNDERLINE.match(following)
                if underline:
                    is_heading = True
                    skip_next = True
                    _push(headings, 1 if underline.group(1)[0] == "=" else 2, text)

        blocks.append(
            TextBlock(
                text=text,
                location=SourceLocation(heading_path=tuple(headings)),
                block_index=index,
                is_heading=is_heading,
            )
        )
    return tuple(blocks)


def _extract_docx(path: Path) -> tuple[TextBlock, ...]:
    """Word paragraphs, split by their heading styles.

    The style carries the outline level, so the path nests the way the document
    does instead of keeping only the most recent heading.
    """
    from docx import Document

    document = Document(path)
    blocks: list[TextBlock] = []
    headings: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        level = _docx_heading_level(paragraph)
        if level:
            _push(headings, level, text)
        blocks.append(
            TextBlock(
                text=text,
                location=SourceLocation(heading_path=tuple(headings)),
                block_index=index,
                is_heading=level is not None,
            )
        )
    return tuple(blocks)


def _docx_heading_level(paragraph) -> int | None:
    """The outline level of a paragraph, or None if it is body text.

    `Title` sits above the numbered headings rather than beside them: it names
    the document, and `Heading 1` divides it. Mapping both to level 1 would let
    the first `Heading 1` replace the title instead of nesting under it, so
    `Title` takes level 1 and `Heading N` takes N + 1. A document with no title
    loses nothing by it -- with nothing at level 1, `Heading 1` simply becomes
    the first element of the path.
    """
    style = getattr(paragraph, "style", None)
    if style is None:
        return None
    for candidate in (getattr(style, "style_id", None), getattr(style, "name", None)):
        if not candidate:
            continue
        match = _WORD_HEADING.match(str(candidate).strip())
        if not match:
            continue
        if match.group(1).lower() == "title":
            return 1
        return int(match.group(2)) + 1 if match.group(2) else 2
    return None


def _extract_pdf(path: Path) -> tuple[TextBlock, ...]:
    """PDF lines, tagged with the page they came from.

    A PDF has no reliable inline heading marker -- a heading is a visual weight,
    not a structure -- so the only trustworthy outline is the bookmark tree, and
    it resolves to a page rather than to a line. Where a document has one, every
    line on a page inherits the path of the last bookmark at or before it; where
    it does not, pages remain the only division.
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    headings_by_page = _pdf_headings_by_page(reader)
    blocks: list[TextBlock] = []
    for page_index, page in enumerate(reader.pages, start=1):
        heading_path = headings_by_page.get(page_index, ())
        for line in (page.extract_text() or "").splitlines():
            text = line.strip()
            if text:
                blocks.append(
                    TextBlock(
                        text=text,
                        location=SourceLocation(page_index, page_index, heading_path),
                        block_index=len(blocks),
                    )
                )
    return tuple(blocks)


def _pdf_outline(reader, items, level: int = 1):
    """Flatten pypdf's nested outline into (level, title, page number).

    Nesting is how pypdf expresses depth: a child list follows the entry it
    belongs to, rather than each entry carrying a level of its own.
    """
    for item in items:
        if isinstance(item, list):
            yield from _pdf_outline(reader, item, level + 1)
            continue
        try:
            page = reader.get_destination_page_number(item) + 1
        except Exception:  # noqa: BLE001 - a bookmark pointing nowhere is skipped, not fatal
            continue
        title = str(getattr(item, "title", "") or "").strip()
        if title:
            yield level, title, page


def _pdf_headings_by_page(reader) -> dict[int, tuple[str, ...]]:
    """The bookmark path in effect on each page, empty when there are none."""
    try:
        entries = list(_pdf_outline(reader, reader.outline))
    except Exception:  # noqa: BLE001 - a malformed outline must not fail the document
        return {}
    if not entries:
        return {}

    per_page: dict[int, tuple[str, ...]] = {}
    headings: list[str] = []
    for level, title, page in entries:
        _push(headings, max(1, level), title)
        per_page[page] = tuple(headings)

    # A bookmark stays in effect until the next one, so pages between two
    # bookmarks inherit the earlier path rather than losing it.
    current: tuple[str, ...] = ()
    for page in range(1, len(reader.pages) + 1):
        current = per_page.get(page, current)
        per_page[page] = current
    return per_page
