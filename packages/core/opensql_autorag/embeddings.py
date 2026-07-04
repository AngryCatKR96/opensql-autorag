from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError


class HashEmbeddingProvider:
    def __init__(self, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
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

    def embed(self, text: str) -> list[float]:
        prefixed = f"query: {text}" if len(text.split()) < 80 else f"passage: {text}"
        vector = self.model.encode(prefixed, normalize_embeddings=True)
        return [float(value) for value in vector]


def validate_dimension(configured: int, observed: int, column: int) -> None:
    if configured != observed or observed != column:
        raise ValueError(
            "Embedding dimension mismatch: "
            f"configured={configured}, observed={observed}, column={column}"
        )
