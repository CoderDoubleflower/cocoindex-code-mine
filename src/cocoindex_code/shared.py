"""Shared context keys, embedder factory, and CodeChunk schema."""

from __future__ import annotations

import hashlib
import logging
import os
import pathlib
import re
from dataclasses import dataclass
from typing import Annotated, Protocol

import cocoindex as coco
import numpy as np
import numpy.typing as npt
from cocoindex.connectors import sqlite
from cocoindex.resources.schema import VectorSchema

from .settings import DEFAULT_EMBEDDING_PROVIDER, EmbeddingSettings

logger = logging.getLogger(__name__)

_TEST_EMBEDDER_ENV = "COCOINDEX_CODE_TEST_EMBEDDER"
_TEST_EMBED_DIM = 256

# Models that define a "query" prompt for asymmetric retrieval.
_QUERY_PROMPT_MODELS = {"nomic-ai/nomic-embed-code", "nomic-ai/CodeRankEmbed"}


class Embedder(Protocol):
    """Common interface for supported embedding backends."""

    def __coco_memo_key__(self) -> object: ...

    async def __coco_vector_schema__(self) -> VectorSchema: ...

    async def embed(
        self, text: str, prompt_name: str | None = None
    ) -> npt.NDArray[np.float32]: ...


class _DeterministicTestEmbedder:
    """Token-hash embedder for tests that must not call remote APIs."""

    def __coco_memo_key__(self) -> str:
        return "deterministic-test-embedder-v1"

    async def __coco_vector_schema__(self) -> VectorSchema:
        return VectorSchema(dtype=np.dtype("float32"), size=_TEST_EMBED_DIM)

    async def embed(
        self, text: str, prompt_name: str | None = None
    ) -> npt.NDArray[np.float32]:
        vec = np.zeros(_TEST_EMBED_DIM, dtype=np.float32)
        tokens = re.findall(r"[A-Za-z0-9_]+", text.lower())

        if prompt_name:
            tokens.append(f"prompt:{prompt_name.lower()}")
        if not tokens:
            tokens.append("")

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            primary = int.from_bytes(digest[:4], "little") % _TEST_EMBED_DIM
            secondary = int.from_bytes(digest[4:8], "little") % _TEST_EMBED_DIM
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            weight = 1.0 + (digest[9] / 255.0)
            vec[primary] += sign * weight
            vec[secondary] += sign * 0.5

        norm = np.linalg.norm(vec)
        if norm == 0:
            vec[0] = 1.0
            return vec
        return (vec / norm).astype(np.float32)


# Context keys
EMBEDDER = coco.ContextKey[Embedder]("embedder")
SQLITE_DB = coco.ContextKey[sqlite.ManagedConnection]("index_db", tracked=False)
CODEBASE_DIR = coco.ContextKey[pathlib.Path]("codebase", tracked=False)

# Module-level variable — set by daemon at startup (needed for CodeChunk annotation).
embedder: Embedder | None = None

# Query prompt name — set alongside embedder by create_embedder().
query_prompt_name: str | None = None


def create_embedder(settings: EmbeddingSettings) -> Embedder:
    """Create and return an embedder instance based on settings.

    Also sets the module-level ``embedder`` and ``query_prompt_name`` variables.
    """
    global embedder, query_prompt_name

    if os.environ.get(_TEST_EMBEDDER_ENV) == "1":
        instance: Embedder = _DeterministicTestEmbedder()
        query_prompt_name = None
        logger.info("Embedding model: deterministic test embedder")
    else:
        if settings.provider != DEFAULT_EMBEDDING_PROVIDER:
            raise ValueError(
                f"Unsupported embedding provider {settings.provider!r}. "
                f"Only {DEFAULT_EMBEDDING_PROVIDER!r} is supported."
            )

        from cocoindex.ops.litellm import LiteLLMEmbedder

        instance = LiteLLMEmbedder(settings.model)
        query_prompt_name = "query" if settings.model in _QUERY_PROMPT_MODELS else None
        logger.info("Embedding model (LiteLLM): %s", settings.model)

    embedder = instance
    return instance


@dataclass
class CodeChunk:
    """Schema for storing code chunks in SQLite."""

    id: int
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    embedding: Annotated[npt.NDArray[np.float32], EMBEDDER]
