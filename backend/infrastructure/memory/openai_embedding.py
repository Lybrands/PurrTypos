"""Single-attempt OpenAI-compatible Embedding adapter for managed memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from openai import AsyncOpenAI

from purra.cancellation import await_with_cancellation, raise_if_stopped
from purra_mem0 import EmbeddingResult
from utils.url import normalize_base_url


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingConfiguration:
    model: str
    api_key: str
    base_url: str
    dimensions: int


class OpenAIEmbeddingGateway:
    def __init__(
        self,
        configuration: OpenAIEmbeddingConfiguration,
        *,
        client_factory: Callable = AsyncOpenAI,
    ) -> None:
        if not isinstance(configuration, OpenAIEmbeddingConfiguration):
            raise TypeError(
                "configuration must be OpenAIEmbeddingConfiguration"
            )
        base_url = normalize_base_url(configuration.base_url)
        if not base_url:
            raise ValueError("Embedding base URL is required")
        self._configuration = configuration
        self._client = client_factory(
            api_key=configuration.api_key,
            base_url=base_url,
            max_retries=0,
        )
        self._closed = False

    async def embed(self, texts: Sequence[str], signal) -> EmbeddingResult:
        if self._closed:
            raise RuntimeError("memory_embedding_gateway_closed")
        values = tuple(texts)
        if not values or any(not isinstance(text, str) or not text for text in values):
            raise ValueError("Embedding input must contain non-empty strings")
        raise_if_stopped(signal)
        response = await await_with_cancellation(
            self._client.embeddings.create(
                model=self._configuration.model,
                input=list(values),
                dimensions=self._configuration.dimensions,
                encoding_format="float",
            ),
            signal,
        )
        raise_if_stopped(signal)
        ordered = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in ordered] != list(range(len(values))):
            raise RuntimeError("memory_embedding_provider_contract")
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        if type(input_tokens) is not int or input_tokens < 0:
            input_tokens = None
        return EmbeddingResult(
            tuple(tuple(item.embedding) for item in ordered),
            input_tokens=input_tokens,
        )

    async def close(self) -> None:
        if not self._closed:
            await self._client.close()
            self._closed = True

