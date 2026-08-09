from __future__ import annotations

from collections.abc import Iterable

from opensql_autorag.domain import Chunk, SourceLocation, TextBlock
from opensql_autorag.hash_utils import content_hash, normalize_text, stable_key


class SemanticChunker:
    def __init__(self, target_tokens: int = 220, overlap_tokens: int = 30) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, document_id: str, blocks: Iterable[TextBlock]) -> tuple[Chunk, ...]:
        chunks: list[Chunk] = []
        buffer_words: list[str] = []
        buffer_location: SourceLocation | None = None
        buffer_has_body = False

        for block in blocks:
            words = normalize_text(block.text).split()
            if not words:
                continue
            if buffer_location is None:
                buffer_location = block.location
            section_changed = block.location.heading_path != buffer_location.heading_path
            if buffer_words and (
                section_changed or len(buffer_words) + len(words) > self.target_tokens
            ):
                if section_changed and not buffer_has_body:
                    # A heading whose section has no text of its own, such as a
                    # document title immediately followed by its first
                    # subsection. On its own it would be a chunk of two or three
                    # words competing for a result slot, so its words go to the
                    # section beneath it instead of forming one.
                    buffer_location = block.location
                else:
                    self._flush(document_id, chunks, buffer_words, buffer_location)
                    # Overlap carries context across a split made inside one
                    # section. Across a section boundary there is no context to
                    # carry: the new chunk would open with the previous section's
                    # words while its heading path claims the new section, and an
                    # edit to one section would then invalidate the next
                    # section's chunk as well.
                    buffer_words = (
                        []
                        if section_changed
                        else buffer_words[-self.overlap_tokens :]
                        if self.overlap_tokens
                        else []
                    )
                    buffer_location = block.location
                    buffer_has_body = False
            buffer_words.extend(words)
            buffer_has_body = buffer_has_body or not block.is_heading

            while len(buffer_words) >= self.target_tokens:
                window = buffer_words[: self.target_tokens]
                self._flush(document_id, chunks, window, buffer_location)
                buffer_words = buffer_words[self.target_tokens - self.overlap_tokens :]

        if buffer_words and buffer_location is not None:
            self._flush(document_id, chunks, buffer_words, buffer_location)

        return tuple(chunks)

    def _flush(
        self,
        document_id: str,
        chunks: list[Chunk],
        words: list[str],
        location: SourceLocation,
    ) -> None:
        text = " ".join(words)
        index = len(chunks)
        chunks.append(
            Chunk(
                stable_key=stable_key(document_id, location.heading_path, index, text),
                text=text,
                content_hash=content_hash(text),
                chunk_index=index,
                location=location,
                token_estimate=len(words),
            )
        )
