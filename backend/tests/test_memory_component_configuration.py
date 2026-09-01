from __future__ import annotations

import pytest

from application.memory_component import (
    MemoryComponentConfigurationError,
    create_memory_component_resource,
    parse_memory_embedding_configuration,
)


class _SettingsDb:
    def __init__(self, value):
        self.value = value

    async def fetch_one(self, query, params):
        assert params == ["memory_embedding_config"]
        return None if self.value is None else {"value": self.value}


def test_memory_embedding_configuration_requires_an_explicit_provider():
    with pytest.raises(MemoryComponentConfigurationError) as caught:
        parse_memory_embedding_configuration(
            {
                "apiProvider": "anthropic",
                "model": "embed",
                "apiKey": "secret",
                "baseUrl": "https://provider.test/v1",
                "dimensions": 4,
            }
        )
    assert caught.value.code == "memory_embedding_provider_unsupported"


@pytest.mark.asyncio
async def test_unconfigured_memory_does_not_choose_a_paid_provider(tmp_path):
    assert (
        await create_memory_component_resource(
            _SettingsDb(None),
            data_dir=tmp_path,
        )
        is None
    )
    assert not (tmp_path / "memory-component-v1").exists()


@pytest.mark.asyncio
async def test_configured_memory_uses_the_persisted_dimensions(tmp_path):
    captured = {}

    class Client:
        pass

    class EmbeddingClient:
        def __init__(self, **options):
            self.options = options

        async def close(self):
            return None

    def client_factory(*, config, embedding_dims):
        captured.update(config=config, embedding_dims=embedding_dims)
        return Client()

    resource = await create_memory_component_resource(
        _SettingsDb(
            {
                "apiProvider": "openai",
                "model": "text-embedding-test",
                "apiKey": "secret",
                "baseUrl": "https://provider.test/v1",
                "dimensions": 12,
            }
        ),
        data_dir=tmp_path,
        client_factory=client_factory,
        embedding_client_factory=EmbeddingClient,
    )
    assert resource is not None
    assert captured["embedding_dims"] == 12
    assert captured["config"]["vector_store"]["config"][
        "embedding_model_dims"
    ] == 12
    await resource.embedding_gateway.close()


@pytest.mark.asyncio
async def test_storage_initialization_failure_is_redacted(tmp_path):
    def unavailable(**_kwargs):
        raise OSError("lock details")

    class EmbeddingClient:
        def __init__(self, **_options):
            pass

        async def close(self):
            return None

    with pytest.raises(Exception) as caught:
        await create_memory_component_resource(
            _SettingsDb(
                {
                    "apiProvider": "openai",
                    "model": "embed",
                    "apiKey": "secret",
                    "baseUrl": "https://provider.test/v1",
                    "dimensions": 4,
                }
            ),
            data_dir=tmp_path,
            client_factory=unavailable,
            embedding_client_factory=EmbeddingClient,
        )
    assert str(caught.value) == "memory_storage_unavailable"
