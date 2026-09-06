from __future__ import annotations

import json

import pytest

from infrastructure.memory import (
    MemoryComponentResource,
    MemoryResourceConfiguration,
    MemoryResourceError,
)
from purra.contracts import AgentMessage, ModelCompletion, ModelTokenUsage
from purra.retrieval import RetrievalRequest
from purra_mem0 import (
    EmbeddingResult,
    MemoryBudget,
    MemoryProviders,
    MemorySource,
)


def _providers(key: str) -> MemoryProviders:
    async def complete(messages, cap, signal):
        return ModelCompletion(
            message=AgentMessage(
                role="assistant",
                content=json.dumps({"memory": []}),
            ),
            model="fixture",
            finish_reason="stop",
            applied_generation_limit=cap,
            usage=ModelTokenUsage(1, 1),
        )

    async def embed(texts, signal):
        return EmbeddingResult(
            tuple((1.0, 0.0, 0.0, 0.0) for _ in texts),
            input_tokens=sum(len(text) for text in texts),
        )

    return MemoryProviders(
        budget=MemoryBudget(
            key=key,
            max_llm_calls=2,
            max_embedding_calls=20,
            max_input_chars=10_000,
            max_output_tokens=1_024,
            result_capacity_target_tokens=512,
        ),
        complete=complete,
        embed=embed,
    )


@pytest.mark.asyncio
async def test_real_sdk_resource_restarts_with_the_same_scoped_memory(tmp_path):
    configuration = MemoryResourceConfiguration(tmp_path, 4)
    first = MemoryComponentResource(configuration)
    async with first.memory(
        user_id="desktop-user",
        project_id="book-1",
        providers=_providers("book-1-budget"),
    ) as memory:
        created = await memory.add(
            "用户希望使用中文回复。",
            source=MemorySource("manual:1", "1"),
            key="manual:1:create",
        )
    await first.close()

    second = MemoryComponentResource(configuration)
    async with second.memory(
        user_id="desktop-user",
        project_id="book-1",
        providers=_providers("book-1-budget"),
    ) as memory:
        restored = await memory.get(created.ids[0])
        hits = await memory.retrieve(
            RetrievalRequest(query="中文", limit=4)
        )
        assert restored is not None
        assert restored.text == "用户希望使用中文回复。"
        assert [hit.id for hit in hits] == list(created.ids)
    await second.close()


@pytest.mark.asyncio
async def test_resource_rejects_dimension_change_without_opening_the_store(tmp_path):
    first = MemoryComponentResource(MemoryResourceConfiguration(tmp_path, 4))
    first.initialize()
    await first.close()

    changed = MemoryComponentResource(MemoryResourceConfiguration(tmp_path, 8))
    with pytest.raises(MemoryResourceError) as caught:
        changed.initialize()
    assert caught.value.code == "memory_embedding_dimensions_changed"
    await changed.close()


@pytest.mark.asyncio
async def test_resource_rejects_new_scope_after_shutdown(tmp_path):
    resource = MemoryComponentResource(MemoryResourceConfiguration(tmp_path, 4))
    await resource.close()

    with pytest.raises(MemoryResourceError) as caught:
        async with resource.memory(
            user_id="desktop-user",
            project_id="book-1",
            providers=_providers("closed"),
        ):
            pass
    assert caught.value.code == "memory_component_closed"


def test_resource_requires_an_explicit_embedding_gateway_for_providers(tmp_path):
    resource = MemoryComponentResource(MemoryResourceConfiguration(tmp_path, 4))

    with pytest.raises(MemoryResourceError) as caught:
        resource.providers(
            budget=_providers("budget").budget,
            complete=_providers("budget").complete,
        )
    assert caught.value.code == "memory_embedding_unconfigured"
