from dataclasses import replace
import json

import pytest

from application.prepared_read_context import PreparedReadContextProvider
from domains.read_materials import ReadMaterial, READ_MATERIAL_SOURCE
from purra.contracts import AgentMessage, ContextBudget
from purra.evidence import CONTEXT_EVIDENCE_RECEIPTS_KEY
from tests.test_shared_agent_context import _DomainContextProvider, _request


@pytest.mark.asyncio
async def test_prepared_material_is_whole_untrusted_deduplicated_and_budgeted():
    material = ReadMaterial("chapter-1:v1", "getChapterContent", {"chapterId": "chapter-1"},
                            json.dumps({"plainText": "完整正文" * 500}, ensure_ascii=False))

    async def load(request, signal):
        return (material, material)

    provider = PreparedReadContextProvider(_DomainContextProvider(), load)
    budget = ContextBudget(32_768, 4096, 1024, 1024, provider_input_tokens=26_624)
    request = _request()
    bundle = await provider.build_context(request, budget)
    supplied = [block for block in bundle.blocks if block.name.startswith("read_material_")]
    assert len(supplied) == 1
    block = supplied[0]
    assert json.loads(block.content)["result"] == json.loads(material.content)
    assert block.untrusted is True
    receipt = block.host_metadata[CONTEXT_EVIDENCE_RECEIPTS_KEY][0]
    assert receipt["source"] == READ_MATERIAL_SOURCE
    assert receipt["complete"] is True

    carried = AgentMessage(role="developer", content=block.content,
                           attributes={"context_name": block.name})
    reused = await provider.build_context(replace(request, messages=(carried, *request.messages)), budget)
    assert not any(item.name == block.name for item in reused.blocks)
    compressed = replace(carried, content="章节正文摘要")
    restored = await provider.build_context(replace(request, messages=(compressed,)), budget)
    assert any(item.content == block.content for item in restored.blocks)
    constrained = await provider.build_context(request, replace(budget, provider_input_tokens=200))
    assert not any(item.name.startswith("read_material_") for item in constrained.blocks)
    planning = await provider.build_planning_context(request, budget)
    assert not any(item.name.startswith("read_material_") for item in planning.blocks)
