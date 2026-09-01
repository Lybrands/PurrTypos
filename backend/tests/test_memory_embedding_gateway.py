from __future__ import annotations

from types import SimpleNamespace

import pytest

from infrastructure.memory import (
    OpenAIEmbeddingConfiguration,
    OpenAIEmbeddingGateway,
)


class _Embeddings:
    def __init__(self):
        self.requests = []

    async def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                SimpleNamespace(index=0, embedding=[1.0, 0.0]),
            ],
            usage=SimpleNamespace(prompt_tokens=7),
        )


class _Client:
    def __init__(self, **options):
        self.options = options
        self.embeddings = _Embeddings()
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_embedding_gateway_has_one_explicit_bounded_provider_path():
    clients = []

    def factory(**options):
        client = _Client(**options)
        clients.append(client)
        return client

    gateway = OpenAIEmbeddingGateway(
        OpenAIEmbeddingConfiguration(
            model="embed-test",
            api_key="secret",
            base_url="https://provider.test/v1",
            dimensions=2,
        ),
        client_factory=factory,
    )
    result = await gateway.embed(("甲", "乙"), None)

    assert result.vectors == ((1.0, 0.0), (0.0, 1.0))
    assert result.input_tokens == 7
    assert clients[0].options == {
        "api_key": "secret",
        "base_url": "https://provider.test/v1",
        "max_retries": 0,
    }
    assert clients[0].embeddings.requests == [{
        "model": "embed-test",
        "input": ["甲", "乙"],
        "dimensions": 2,
        "encoding_format": "float",
    }]

    await gateway.close()
    assert clients[0].closed is True

