"""Configure the optional PurrA memory resource from persisted host settings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from infrastructure.memory import (
    MemoryComponentResource,
    MemoryResourceConfiguration,
    OpenAIEmbeddingConfiguration,
    OpenAIEmbeddingGateway,
)
from services.model_settings_service import get_setting_value


class MemoryComponentConfigurationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MemoryEmbeddingConfiguration:
    model: str
    api_key: str
    base_url: str
    dimensions: int


def _required_text(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MemoryComponentConfigurationError(
            f"memory_embedding_{key}_invalid"
        )
    return value.strip()


def parse_memory_embedding_configuration(
    value: Any,
) -> MemoryEmbeddingConfiguration | None:
    if value is None or value == "":
        return None
    if not isinstance(value, Mapping):
        raise MemoryComponentConfigurationError(
            "memory_embedding_configuration_invalid"
        )
    if value.get("apiProvider") != "openai":
        raise MemoryComponentConfigurationError(
            "memory_embedding_provider_unsupported"
        )
    dimensions = value.get("dimensions")
    if type(dimensions) is not int or not 1 <= dimensions <= 65_536:
        raise MemoryComponentConfigurationError(
            "memory_embedding_dimensions_invalid"
        )
    return MemoryEmbeddingConfiguration(
        model=_required_text(value, "model"),
        api_key=_required_text(value, "apiKey"),
        base_url=_required_text(value, "baseUrl"),
        dimensions=dimensions,
    )


async def create_memory_component_resource(
    db,
    *,
    data_dir: Path,
    client_factory: Callable | None = None,
    embedding_client_factory: Callable | None = None,
) -> MemoryComponentResource | None:
    raw = await get_setting_value(db, "memory_embedding_config")
    embedding = parse_memory_embedding_configuration(raw)
    if embedding is None:
        return None
    gateway_kwargs = (
        {}
        if embedding_client_factory is None
        else {"client_factory": embedding_client_factory}
    )
    embedding_gateway = OpenAIEmbeddingGateway(
        OpenAIEmbeddingConfiguration(
            model=embedding.model,
            api_key=embedding.api_key,
            base_url=embedding.base_url,
            dimensions=embedding.dimensions,
        ),
        **gateway_kwargs,
    )
    kwargs = {} if client_factory is None else {"client_factory": client_factory}
    resource = MemoryComponentResource(
        MemoryResourceConfiguration(data_dir, embedding.dimensions),
        embedding_gateway=embedding_gateway,
        **kwargs,
    )
    try:
        await asyncio.to_thread(resource.initialize)
    except BaseException:
        await embedding_gateway.close()
        raise
    return resource


__all__ = [
    "MemoryComponentConfigurationError",
    "MemoryEmbeddingConfiguration",
    "create_memory_component_resource",
    "parse_memory_embedding_configuration",
]
