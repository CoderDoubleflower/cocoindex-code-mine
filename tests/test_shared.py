"""Unit tests for the embedder factory."""

from __future__ import annotations

import numpy as np
import pytest

from cocoindex_code.settings import EmbeddingSettings
from cocoindex_code.shared import create_embedder


@pytest.mark.parametrize("text", ["fibonacci search", "database connection"])
async def test_create_embedder_uses_deterministic_test_embedder(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    monkeypatch.setenv("COCOINDEX_CODE_TEST_EMBEDDER", "1")

    embedder = create_embedder(
        EmbeddingSettings(provider="litellm", model="text-embedding-3-small")
    )
    vec1 = await embedder.embed(text)
    vec2 = await embedder.embed(text)

    assert np.array_equal(vec1, vec2)
    assert vec1.dtype == np.float32


def test_create_embedder_rejects_non_litellm_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COCOINDEX_CODE_TEST_EMBEDDER", raising=False)

    with pytest.raises(ValueError, match="Only 'litellm' is supported"):
        create_embedder(EmbeddingSettings(provider="legacy-provider", model="legacy-model"))
