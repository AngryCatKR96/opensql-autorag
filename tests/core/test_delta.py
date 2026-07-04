from opensql_autorag.delta import DeltaPlanner
from opensql_autorag.domain import Chunk, ChunkDecision, SourceLocation
from opensql_autorag.hash_utils import content_hash, stable_key


def make_chunk(index: int, text: str) -> Chunk:
    location = SourceLocation(1, 1, ("Guide",))
    return Chunk(
        stable_key=stable_key("doc-1", location.heading_path, index, text),
        text=text,
        content_hash=content_hash(text),
        chunk_index=index,
        location=location,
        token_estimate=len(text.split()),
    )


def test_delta_reuses_unchanged_chunks():
    previous = (make_chunk(0, "same content"),)
    current = (make_chunk(0, "same content"),)

    plan = DeltaPlanner().plan(previous, current)

    assert plan.reused_count == 1
    assert plan.embedded_count == 0
    assert plan.chunks[0].decision == ChunkDecision.REUSE


def test_delta_embeds_changed_and_retires_missing_chunks():
    previous = (make_chunk(0, "old content"), make_chunk(1, "removed content"))
    current = (make_chunk(0, "new content"),)

    plan = DeltaPlanner().plan(previous, current)

    assert plan.embedded_count == 1
    assert plan.retired_count == 2
    assert [item.decision for item in plan.chunks] == [
        ChunkDecision.EMBED,
        ChunkDecision.RETIRE,
        ChunkDecision.RETIRE,
    ]
