import pytest
from opensql_autorag.embeddings import (
    HashEmbeddingProvider,
    create_embedding_provider,
    validate_dimension,
)


def test_hash_embedding_provider_returns_configured_dimension():
    provider = HashEmbeddingProvider(dimension=384)

    vector = provider.embed("OpenSQL pgvector", role="passage")

    assert len(vector) == 384
    assert all(isinstance(value, float) for value in vector)


def test_hash_provider_is_symmetric_across_roles():
    """A hash carries no notion of a question versus a document.

    Giving the two roles different vectors would separate them for a reason no
    search could act on, which is worse than a stand-in that admits it is one.
    """
    provider = HashEmbeddingProvider(dimension=384)

    assert provider.embed("same text", role="query") == provider.embed("same text", role="passage")


def test_validate_dimension_rejects_mismatch():
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        validate_dimension(configured=384, observed=1024, column=384)


def test_role_is_required_so_it_cannot_be_defaulted_wrong():
    """The real provider takes no default: the caller always knows the role.

    A default is how the length heuristic this replaced went unnoticed -- every
    call site looked correct while short documents were being marked as queries.
    """
    import inspect

    from opensql_autorag.embeddings import SentenceTransformerEmbeddingProvider

    role = inspect.signature(SentenceTransformerEmbeddingProvider.embed).parameters["role"]
    assert role.default is inspect.Parameter.empty


MODEL = "intfloat/multilingual-e5-small"


def _model_is_cached() -> bool:
    """Whether the model is already on disk, so no test downloads 470MB."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return isinstance(try_to_load_from_cache(MODEL, "config.json"), str)


def test_e5_gives_a_different_vector_per_role():
    """The prefix is not decoration: it changes the vector for identical words.

    Skipped unless the model is already cached, following the same rule as the
    database fixture: a test suite should not reach for the network on its own.
    """
    pytest.importorskip("sentence_transformers")
    if not _model_is_cached():
        pytest.skip(f"{MODEL} is not in the local cache")

    provider = create_embedding_provider(
        provider="sentence-transformers",
        model_name=MODEL,
        dimension=384,
    )
    text = "page the platform team and open a severity two incident"

    as_query = provider.embed(text, role="query")
    as_passage = provider.embed(text, role="passage")

    assert as_query != as_passage
    similarity = sum(a * b for a, b in zip(as_query, as_passage, strict=True))
    # Close, because it is the same sentence -- but far enough from 1.0 that
    # mixing the two through one index is not a rounding difference.
    assert 0.90 < similarity < 0.995
