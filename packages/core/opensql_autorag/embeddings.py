from __future__ import annotations

import hashlib
import math
import struct
from typing import Literal, Protocol

HASH_MODEL_NAME = "sha256-deterministic"

# What a piece of text is being embedded as. Asymmetric models -- the e5 family
# among them -- are trained with the two roles marked, and give a different
# vector for the same words depending on which one is claimed. There is no
# default: the caller always knows whether it holds a question or a document,
# and guessing is what this parameter exists to stop.
EmbeddingRole = Literal["query", "passage"]


class EmbeddingProvider(Protocol):
    model_name: str
    dimension: int

    def embed(self, text: str, role: EmbeddingRole) -> list[float]:
        raise NotImplementedError


class HashEmbeddingProvider:
    def __init__(self, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.model_name = HASH_MODEL_NAME
        self.dimension = dimension

    def embed(self, text: str, role: EmbeddingRole = "passage") -> list[float]:
        """A deterministic vector, ignoring the role.

        This provider exists so the platform runs with no model download, and a
        symmetric fake is the honest stand-in: a hash carries no notion of a
        question versus a document, and pretending otherwise would give the two
        roles vectors that differ for no reason a search could use.
        """
        del role
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            for offset in range(0, len(digest), 4):
                if len(values) == self.dimension:
                    break
                integer = struct.unpack(">I", digest[offset : offset + 4])[0]
                values.append((integer / 2**32) * 2 - 1)
            counter += 1
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str = "intfloat/multilingual-e5-small") -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.dimension = int(self.model.get_sentence_embedding_dimension())

    def embed(self, text: str, role: EmbeddingRole) -> list[float]:
        """Encode with the role marked, which is what e5 was trained on.

        The prefix comes from the caller rather than from the text, because it
        describes what the text is for and nothing about the text reveals that.
        Choosing it by length -- which this did -- marks a short document as a
        query, so a corpus ends up with both prefixes mixed through it and the
        two halves are no longer compared on the same footing.
        """
        vector = self.model.encode(f"{role}: {text}", normalize_embeddings=True)
        return [float(value) for value in vector]


def create_embedding_provider(
    provider: str,
    model_name: str,
    dimension: int,
) -> EmbeddingProvider:
    """Build the embedding provider selected by configuration.

    `dimension` only applies to the hash provider; a real model reports its own.
    """
    if provider == "hash":
        return HashEmbeddingProvider(dimension=dimension)
    if provider == "sentence-transformers":
        return SentenceTransformerEmbeddingProvider(model_name=model_name)
    raise ValueError(
        f"unknown embedding provider: {provider!r} (expected 'hash' or 'sentence-transformers')"
    )


def validate_dimension(configured: int, observed: int, column: int) -> None:
    if configured != observed or observed != column:
        raise ValueError(
            "Embedding dimension mismatch: "
            f"configured={configured}, observed={observed}, column={column}"
        )
