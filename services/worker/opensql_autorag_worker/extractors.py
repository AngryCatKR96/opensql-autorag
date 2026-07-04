from __future__ import annotations

from pathlib import Path

from opensql_autorag.domain import SourceLocation, TextBlock


def extract_blocks(path: Path) -> tuple[TextBlock, ...]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    if suffix in {".md", ".txt"}:
        return _extract_plain_text(path)
    raise ValueError(f"unsupported file type: {suffix}")


def _extract_plain_text(path: Path) -> tuple[TextBlock, ...]:
    blocks: list[TextBlock] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        text = line.strip()
        if text:
            blocks.append(TextBlock(text=text, location=SourceLocation(), block_index=index))
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
