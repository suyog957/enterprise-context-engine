from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from hashlib import blake2b
from math import sqrt
from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def dimensions(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FeatureHashEmbeddingProvider:
    """Deterministic lexical feature hashing for offline tests and local fallback."""

    def __init__(self, dimensions: int = 384) -> None:
        if dimensions < 8:
            raise ValueError("Embedding dimensions must be at least 8")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        counts = Counter(token.casefold() for token in text.split() if token.strip())
        for token, count in counts.items():
            digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, byteorder="big") % self._dimensions
            vector[index] += float(count)
        magnitude = sqrt(sum(value * value for value in vector))
        return [value / magnitude for value in vector] if magnitude else vector


class SentenceTransformerEmbeddingProvider:
    def __init__(self, model_name: str, dimensions: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Install the 'embeddings' extra to use sentence-transformer embeddings"
            ) from error
        self._model = SentenceTransformer(model_name)
        self._dimensions = dimensions
        model_dimensions = self._model.get_sentence_embedding_dimension()
        if model_dimensions != dimensions:
            raise ValueError(
                f"Configured embedding dimensions {dimensions} do not match "
                f"model dimensions {model_dimensions}"
            )

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [vector.tolist() for vector in vectors]
