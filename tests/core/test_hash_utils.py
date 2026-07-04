from opensql_autorag.hash_utils import content_hash, normalize_text, stable_key


def test_normalize_text_collapses_whitespace():
    assert normalize_text("OpenSQL\n\n  pgvector\t검색") == "OpenSQL pgvector 검색"


def test_content_hash_is_stable_for_formatting_noise():
    assert content_hash("A\n\nB") == content_hash("A B")


def test_stable_key_includes_document_heading_and_index():
    key = stable_key("doc-1", ("Chapter 1", "Vector"), 3, "hello")
    assert key == stable_key("doc-1", ("Chapter 1", "Vector"), 3, "hello")
    assert key != stable_key("doc-1", ("Chapter 1", "Vector"), 4, "hello")
