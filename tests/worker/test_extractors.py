from pathlib import Path

import pytest
from opensql_autorag_worker.extractors import extract_blocks


def test_extract_text_file_to_blocks(tmp_path: Path):
    path = tmp_path / "guide.txt"
    path.write_text("Intro\nOpenSQL stores vectors.", encoding="utf-8")

    blocks = extract_blocks(path)

    assert len(blocks) == 2
    assert blocks[0].text == "Intro"
    assert blocks[1].text == "OpenSQL stores vectors."


def test_plain_text_has_no_heading_path(tmp_path: Path):
    """A .txt file has no structure to read, so nothing is invented for it."""
    path = tmp_path / "notes.txt"
    path.write_text("# not a heading\njust a line", encoding="utf-8")

    blocks = extract_blocks(path)

    assert [block.location.heading_path for block in blocks] == [(), ()]


def test_markdown_headings_nest_into_a_path(tmp_path: Path):
    path = tmp_path / "runbook.md"
    path.write_text(
        "# Runbook\n\nPreamble.\n\n## Alerts\n\nPage on latency.\n\n"
        "### Escalation\n\nOpen an incident.\n\n## Rollback\n\nDrain a replica.\n",
        encoding="utf-8",
    )

    blocks = extract_blocks(path)
    paths = {block.text: block.location.heading_path for block in blocks}

    assert paths["Preamble."] == ("Runbook",)
    assert paths["Page on latency."] == ("Runbook", "Alerts")
    assert paths["Open an incident."] == ("Runbook", "Alerts", "Escalation")
    # Back up a level: Rollback replaces Alerts rather than nesting under it.
    assert paths["Drain a replica."] == ("Runbook", "Rollback")


def test_markdown_heading_text_is_kept_as_a_block(tmp_path: Path):
    """The section title is content: it is often the words a query matches."""
    path = tmp_path / "policy.md"
    path.write_text("## Expense policy ##\n\nFile within 30 days.\n", encoding="utf-8")

    blocks = extract_blocks(path)

    assert [block.text for block in blocks] == ["Expense policy", "File within 30 days."]


def test_markdown_ignores_headings_inside_a_code_fence(tmp_path: Path):
    """`# comment` in a shell block is a comment, not a section."""
    path = tmp_path / "runbook.md"
    path.write_text(
        "# Runbook\n\n```bash\n# drain the worker\nsystemctl stop worker\n```\n\nDone.\n",
        encoding="utf-8",
    )

    blocks = extract_blocks(path)
    paths = {block.text: block.location.heading_path for block in blocks}

    assert paths["# drain the worker"] == ("Runbook",)
    assert paths["systemctl stop worker"] == ("Runbook",)
    assert paths["Done."] == ("Runbook",)


def test_markdown_skipped_heading_level_does_not_break_the_path(tmp_path: Path):
    path = tmp_path / "skip.md"
    path.write_text("# Top\n\n### Deep\n\nBody.\n", encoding="utf-8")

    blocks = extract_blocks(path)
    paths = {block.text: block.location.heading_path for block in blocks}

    assert paths["Body."] == ("Top", "Deep")


def test_markdown_setext_headings_are_recognised(tmp_path: Path):
    """`Title` over `===` is a heading; the same line over nothing is a paragraph."""
    path = tmp_path / "setext.md"
    path.write_text(
        "Runbook\n=======\n\nPreamble.\n\nAlerts\n------\n\nPage on latency.\n\nNot a heading\n",
        encoding="utf-8",
    )

    blocks = extract_blocks(path)
    paths = {block.text: block.location.heading_path for block in blocks}

    assert paths["Preamble."] == ("Runbook",)
    assert paths["Page on latency."] == ("Runbook", "Alerts")
    assert paths["Not a heading"] == ("Runbook", "Alerts")
    # The underline is markup, not content.
    assert "=======" not in paths and "------" not in paths


def test_docx_heading_levels_nest(tmp_path: Path):
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("Employee handbook", style="Title")
    document.add_paragraph("Travel", style="Heading 1")
    document.add_paragraph("Book through the portal.")
    document.add_paragraph("Flights", style="Heading 2")
    document.add_paragraph("Economy under six hours.")
    document.add_paragraph("Equipment", style="Heading 1")
    document.add_paragraph("Refresh every three years.")
    path = tmp_path / "handbook.docx"
    document.save(path)

    paths = {block.text: block.location.heading_path for block in extract_blocks(path)}

    assert paths["Book through the portal."] == ("Employee handbook", "Travel")
    assert paths["Economy under six hours."] == ("Employee handbook", "Travel", "Flights")
    # Back up a level: Equipment replaces Travel rather than nesting under it.
    assert paths["Refresh every three years."] == ("Employee handbook", "Equipment")


def write_pdf(path: Path, pages: list[str], outline: list[tuple[int, str]] | None = None) -> None:
    """A PDF with one line per page, and optional bookmarks by page index.

    Building the fixture takes a writer, and neither pypdf nor pdfminer draws
    text; reportlab does, and is BSD like the rest of the stack.
    """
    import io

    from pypdf import PdfWriter
    from reportlab.pdfgen import canvas

    drawn = io.BytesIO()
    pdf = canvas.Canvas(drawn)
    for line in pages:
        pdf.drawString(72, 720, line)
        pdf.showPage()
    pdf.save()
    drawn.seek(0)

    writer = PdfWriter(clone_from=drawn)
    # Each bookmark points at a successive page, so the nth entry lands on page n.
    parents: dict[int, object] = {}
    for page_index, (level, title) in enumerate(outline or []):
        parents[level] = writer.add_outline_item(title, page_index, parent=parents.get(level - 1))
    with path.open("wb") as handle:
        writer.write(handle)


def test_pdf_without_bookmarks_still_records_pages(tmp_path: Path):
    pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    path = tmp_path / "plain.pdf"
    write_pdf(path, ["Plain body text."])

    blocks = extract_blocks(path)

    assert blocks[0].location.page_start == 1
    assert blocks[0].location.heading_path == ()


def test_pdf_pages_inherit_the_bookmark_in_effect(tmp_path: Path):
    pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    path = tmp_path / "outlined.pdf"
    write_pdf(
        path,
        [f"Body of page {n + 1}." for n in range(3)],
        outline=[(1, "Operations"), (2, "Failover")],
    )

    paths = {block.text: block.location.heading_path for block in extract_blocks(path)}

    assert paths["Body of page 1."] == ("Operations",)
    assert paths["Body of page 2."] == ("Operations", "Failover")
    # Page 3 has no bookmark of its own and keeps the one still in effect.
    assert paths["Body of page 3."] == ("Operations", "Failover")
