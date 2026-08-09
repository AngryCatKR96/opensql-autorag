from pathlib import Path

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
