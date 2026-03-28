"""
Unified embedding configuration — extracted from duplicate Ollama/OpenAI
detection logic in toolRouter.js:19-28 and mem0Service.js:42-70.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from config import (
    MEM0_EMBED_MODEL,
    MEM0_USE_OLLAMA,
    OLLAMA_HOST,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    provider: str  # "ollama" or "openai"
    model: str
    url: str | None
    api_key: str | None
    base_url: str | None
    dimensions: int

    @classmethod
    def resolve(cls) -> EmbeddingConfig:
        """Determine embedding provider from environment, shared by mem0 and tool_router."""
        if MEM0_USE_OLLAMA:
            return cls(
                provider="ollama",
                model=MEM0_EMBED_MODEL or "nomic-embed-text",
                url=OLLAMA_HOST,
                api_key=None,
                base_url=None,
                dimensions=768,
            )
        if OPENAI_API_KEY:
            return cls(
                provider="openai",
                model=MEM0_EMBED_MODEL or "text-embedding-3-small",
                url=None,
                api_key=OPENAI_API_KEY,
                base_url=OPENAI_BASE_URL or None,
                dimensions=1536,
            )
        raise RuntimeError(
            "mem0 需要 Embedder：请安装并启动 Ollama（推荐），或设置 OPENAI_API_KEY。"
        )

    def service_label(self) -> str:
        """Human-readable label for error messages."""
        if self.provider == "ollama":
            return f"Ollama {self.model}"
        return f"OpenAI {self.model}"


async def get_embedding(text: str, config: EmbeddingConfig | None = None) -> list[float]:
    """Call Ollama or OpenAI embedding API and return the vector.

    Used by the tool router for semantic skill matching.  mem0 handles its
    own embeddings internally — this helper is for *other* call-sites that
    need a raw embedding vector.
    """
    cfg = config or EmbeddingConfig.resolve()

    if cfg.provider == "ollama":
        url = (cfg.url or OLLAMA_HOST).rstrip("/") + "/api/embeddings"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, json={"model": cfg.model, "prompt": text})
            resp.raise_for_status()
            return resp.json()["embedding"]

    # OpenAI-compatible
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
    )
    resp = await client.embeddings.create(model=cfg.model, input=text)
    return resp.data[0].embedding
