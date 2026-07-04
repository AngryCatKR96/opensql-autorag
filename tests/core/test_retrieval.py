from uuid import uuid4

from opensql_autorag.retrieval import RetrievalQuery, RetrievalResult


def test_retrieval_query_defaults_top_k_to_five():
    query = RetrievalQuery(query="OpenSQL")
    assert query.top_k == 5


def test_retrieval_result_carries_source_metadata():
    result = RetrievalResult(
        chunk_id=uuid4(),
        document_id=uuid4(),
        version_id=uuid4(),
        text="OpenSQL stores vectors.",
        score=0.9,
        heading_path="Guide",
        page_start=1,
        page_end=2,
    )

    assert result.heading_path == "Guide"
    assert result.page_start == 1
