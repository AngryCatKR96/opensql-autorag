from pathlib import Path

from opensql_autorag_worker.extractors import extract_blocks


def test_extract_text_file_to_blocks(tmp_path: Path):
    path = tmp_path / "guide.txt"
    path.write_text("Intro\nOpenSQL stores vectors.", encoding="utf-8")

    blocks = extract_blocks(path)

    assert len(blocks) == 2
    assert blocks[0].text == "Intro"
    assert blocks[1].text == "OpenSQL stores vectors."
