from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from opensql_autorag.domain import Chunk, SourceLocation, TextBlock
from opensql_autorag.hash_utils import content_hash, normalize_text, stable_key


@dataclass
class _Section:
    """One heading's worth of text, before any decision about chunking it."""

    heading_path: tuple[str, ...]
    words: list[str] = field(default_factory=list)
    has_body: bool = False
    page_start: int | None = None
    page_end: int | None = None

    def joined(self, other: _Section) -> _Section:
        """Combine two sections, and work out what to call the result.

        A section with no text of its own contributes only its heading, so the
        other one keeps its name: a document title merged with its first
        subsection is still that subsection. Otherwise both contributed content
        and neither may claim the chunk, so it takes what they share.

        The distinction has to be drawn on the body rather than on the paths,
        because merging repeats. Taking the more specific of two paths each time
        would let the last section merged re-specialise a label that had already
        widened -- a chunk holding all of Travel, Equipment and Receipts ends up
        titled "Expense policy / Receipts", naming the one section it is least
        about.
        """
        if not self.has_body:
            path = other.heading_path
        elif not other.has_body:
            path = self.heading_path
        else:
            path = common_heading_path(self.heading_path, other.heading_path)
        return _Section(
            heading_path=path,
            words=self.words + other.words,
            has_body=self.has_body or other.has_body,
            page_start=self.page_start,
            page_end=other.page_end or self.page_end,
        )


def common_heading_path(first: tuple[str, ...], second: tuple[str, ...]) -> tuple[str, ...]:
    """The deepest heading both paths sit under.

    "Expense policy / Travel" and "Expense policy / Equipment" share
    "Expense policy", which is the honest label for a chunk spanning both.
    """
    shared: list[str] = []
    for left, right in zip(first, second, strict=False):
        if left != right:
            break
        shared.append(left)
    return tuple(shared)


class SemanticChunker:
    def __init__(
        self,
        target_tokens: int = 220,
        overlap_tokens: int = 30,
        min_tokens: int | None = None,
    ) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be non-negative and smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        # Below this, a section is folded into the next one rather than becoming
        # a chunk of its own. Splitting by heading is what makes retrieval and
        # delta sync work, but it also turns a one-line section into an eight
        # word chunk, and eight words carry too little for a query to tell them
        # apart -- on the demo corpus every such chunk scored within 0.02 of
        # every other. Derived from the target so a chunker configured small for
        # a test does not merge everything into one chunk.
        self.min_tokens = target_tokens // 5 if min_tokens is None else min_tokens
        if self.min_tokens >= target_tokens:
            raise ValueError("min_tokens must be smaller than target_tokens")

    def chunk(self, document_id: str, blocks: Iterable[TextBlock]) -> tuple[Chunk, ...]:
        """Sections first, then merge the thin ones, then split the long ones.

        Whether a section is worth a chunk of its own depends on the size of the
        one beside it, which is not known while streaming blocks: at the heading
        the next section is a single word long, and by the time its body arrives
        the decision has already been taken. Collecting sections first is what
        makes the choice on the facts, and it is also what lets a thin section at
        the very end fold backwards -- there is nothing after it to fold into.
        """
        sections = self._sections(blocks)
        chunks: list[Chunk] = []
        for section in self._merge_thin(sections):
            self._emit(document_id, chunks, section)
        return tuple(chunks)

    def _sections(self, blocks: Iterable[TextBlock]) -> list[_Section]:
        """Consecutive blocks sharing a heading path, as one section each."""
        sections: list[_Section] = []
        for block in blocks:
            words = normalize_text(block.text).split()
            if not words:
                continue
            path = block.location.heading_path
            if not sections or sections[-1].heading_path != path:
                sections.append(
                    _Section(
                        heading_path=path,
                        words=list(words),
                        has_body=not block.is_heading,
                        page_start=block.location.page_start,
                        page_end=block.location.page_end,
                    )
                )
                continue
            section = sections[-1]
            section.words.extend(words)
            section.has_body = section.has_body or not block.is_heading
            section.page_end = block.location.page_end or section.page_end
        return sections

    def _is_thin(self, section: _Section) -> bool:
        """Too little to stand as a chunk: no text of its own, or barely any.

        A heading with nothing under it is the first case. The second is a one
        line section, which splitting by heading produces readily -- and eight
        words carry too little for a query to tell them apart from any other
        eight, so they belong with their neighbour rather than in a slot of their
        own.
        """
        return not section.has_body or len(section.words) < self.min_tokens

    def _merge_thin(self, sections: list[_Section]) -> list[_Section]:
        """Fold thin sections into a neighbour, where that fits in one chunk.

        Merging is a repair, not an obligation: a one line section next to a long
        one is left alone rather than made into an oversized chunk.
        """
        merged: list[_Section] = []
        for section in sections:
            previous = merged[-1] if merged else None
            if previous is not None and self._is_thin(previous) and self._fits(previous, section):
                merged[-1] = previous.joined(section)
            else:
                merged.append(section)

        # The last section has nothing after it, so it folds backwards instead.
        if len(merged) > 1 and self._is_thin(merged[-1]) and self._fits(merged[-2], merged[-1]):
            tail = merged.pop()
            merged[-1] = merged[-1].joined(tail)
        return merged

    def _fits(self, first: _Section, second: _Section) -> bool:
        return len(first.words) + len(second.words) <= self.target_tokens

    def _emit(self, document_id: str, chunks: list[Chunk], section: _Section) -> None:
        """One chunk per section, or a windowed split when it runs long.

        The overlap applies only inside a section. Across a boundary there is no
        context to carry: the next chunk would open with the previous section's
        words while its heading path claims the new one, and editing either would
        then re-embed both.
        """
        location = SourceLocation(
            page_start=section.page_start,
            page_end=section.page_end,
            heading_path=section.heading_path,
        )
        words = section.words
        start = 0
        while len(words) - start >= self.target_tokens:
            self._flush(document_id, chunks, words[start : start + self.target_tokens], location)
            start += self.target_tokens - self.overlap_tokens
        if start < len(words):
            self._flush(document_id, chunks, words[start:], location)

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
