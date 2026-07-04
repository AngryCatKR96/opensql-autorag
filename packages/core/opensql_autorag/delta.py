from __future__ import annotations

from opensql_autorag.domain import Chunk, ChunkDecision, DeltaPlan, PlannedChunk


class DeltaPlanner:
    def plan(self, previous: tuple[Chunk, ...], current: tuple[Chunk, ...]) -> DeltaPlan:
        previous_by_hash = {chunk.content_hash: chunk for chunk in previous}
        current_hashes = {chunk.content_hash for chunk in current}
        planned: list[PlannedChunk] = []

        for chunk in current:
            previous_chunk = previous_by_hash.get(chunk.content_hash)
            if previous_chunk is not None:
                planned.append(
                    PlannedChunk(
                        chunk=chunk,
                        decision=ChunkDecision.REUSE,
                        previous_stable_key=previous_chunk.stable_key,
                    )
                )
            else:
                planned.append(PlannedChunk(chunk=chunk, decision=ChunkDecision.EMBED))

        for chunk in previous:
            if chunk.content_hash not in current_hashes:
                planned.append(PlannedChunk(chunk=chunk, decision=ChunkDecision.RETIRE))

        return DeltaPlan(chunks=tuple(planned))
