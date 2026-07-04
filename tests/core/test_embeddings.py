import pytest
from opensql_autorag.embeddings import HashEmbeddingProvider, validate_dimension


def test_hash_embedding_provider_returns_configured_dimension():
    provider = HashEmbeddingProvider(dimension=384)

    vector = provider.embed("OpenSQL pgvector")

    assert len(vector) == 384
    assert all(isinstance(value, float) for value in vector)


def test_validate_dimension_rejects_mismatch():
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        validate_dimension(configured=384, observed=1024, column=384)
