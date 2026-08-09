from __future__ import annotations

import re
from pathlib import Path

from opensql_autorag.domain import SourceLocation, TextBlock

# An ATX heading: one to six hashes, the text, and an optional closing run of
# hashes. Outline serialises every heading this way.
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")

# A fenced code block opener or closer.
_CODE_FENCE = re.compile(r"^(`{3,}|~{3,})")


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


def _extract_plain_text(path: Path) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if text:
            blocks.append(TextBlock(text=text, location=SourceLocation(), block_index=index))
    return tuple(blocks)


def _extract_markdown(path: Path) -> tuple[TextBlock, ...]:
    """Blocks carrying the heading each line sits under.

    This is what makes the chunker split a document by section: it starts a new
    chunk wherever the heading path changes, so without a path here a markdown
    file is one undifferentiated run of lines and only ever splits on length.
    That costs the section context in search results, and it costs delta sync
    most of its reuse — edit one paragraph of a document held in a single chunk
    and the whole document is re-embedded.

    It matters more than the file extension suggests. Outline serves every wiki
    page as markdown, so the entire wiki path arrives here.
    """
    blocks: list[TextBlock] = []
    headings: list[str] = []
    fence = ""

    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if not text:
            continue

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
            heading = _ATX_HEADING.match(text)
            if heading:
                # A heading at level N replaces everything from N down, so
                # `## B` after `# A / ## X` yields `A / B`, not `A / X / B`.
                level = len(heading.group(1))
                del headings[level - 1 :]
                text = heading.group(2)
                headings.append(text)

        blocks.append(
            TextBlock(
                text=text,
                location=SourceLocation(heading_path=tuple(headings)),
                block_index=index,
            )
        )
    return tuple(blocks)


def _extract_docx(path: Path) -> tuple[TextBlock, ...]:
    from docx import Document

    document = Document(path)
    blocks: list[TextBlock] = []
    heading_path: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        if paragraph.style and paragraph.style.name.startswith("Heading"):
            heading_path = [text]
        blocks.append(
            TextBlock(
                text=text,
                location=SourceLocation(heading_path=tuple(heading_path)),
                block_index=index,
            )
        )
    return tuple(blocks)


def _extract_pdf(path: Path) -> tuple[TextBlock, ...]:
    import fitz

    blocks: list[TextBlock] = []
    with fitz.open(path) as document:
        for page_index, page in enumerate(document, start=1):
            for line in page.get_text().splitlines():
                text = line.strip()
                if text:
                    blocks.append(
                        TextBlock(
                            text=text,
                            location=SourceLocation(page_index, page_index),
                            block_index=len(blocks),
                        )
                    )
    return tuple(blocks)
