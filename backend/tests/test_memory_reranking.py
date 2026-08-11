from __future__ import annotations

import json
from dataclasses import replace

import pytest

from purra.contracts import (
    AgentMessage,
    MessageRole,
    ModelCompletion,
    ModelFinishReason,
    ModelRequest,
)
from purra.api import AgentModelTaskRunner
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from purra.model_protocol import generic_capability_snapshot
from application.memory_reranking import ModelBackedMemoryReranker
from domains.writing.memory_reranking import MemoryCandidateCard


class _Gateway:
    def __init__(self, selected_id: str):
        self.selected_id = selected_id
        self.invocations = []

    async def complete(self, messages, invocation, signal=None):
        del signal
        self.invocations.append((messages, invocation))
        return ModelCompletion(
            message=AgentMessage(
                role=MessageRole.ASSISTANT,
                content=json.dumps({
                    "selected": [{
                        "id": self.selected_id,
                        "priority": "must_use",
                        "supports": ["人物关系连续性"],
                        "reason": "当前任务延续该关系状态",
                    }],
                    "unresolvedNeeds": [],
                }, ensure_ascii=False),
            ),
            model="judge-model",
            finish_reason=ModelFinishReason.STOP,
        )

    async def stream(self, messages, invocation, signal=None):  # pragma: no cover
        raise NotImplementedError


def _candidate(record_id: str, state: str) -> MemoryCandidateCard:
    return MemoryCandidateCard(
        id=record_id,
        source="story_state",
        kind="relationship_state",
        fact={"state": state},
        subject_id="character-1",
        version=1,
        chapter_id="chapter-1",
        source_excerpt=f"关系状态：{state}",
    )


def _model_request() -> ModelRequest:
    return ModelRequest(
        provider="test",
        model="test-model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            profile_id="test:test-model",
            max_output_tokens=4_096,
        ),
    )


def _model_tasks(gateway) -> AgentModelTaskRunner:
    return AgentModelTaskRunner(
        AgentModelInvocationManager(gateway),
        ModelInvocationContext(run_id="reranker-test-run"),
    )


@pytest.mark.asyncio
async def test_model_reranker_selects_only_host_candidates():
    gateway = _Gateway("record-2")
    reranker = ModelBackedMemoryReranker(_model_tasks(gateway))

    result = await reranker.rerank(
        query="续写两人决裂后的对话",
        candidates=(
            _candidate("record-1", "亲密"),
            _candidate("record-2", "决裂"),
        ),
        story_kinds=("relationship_state",),
        planner_story_kinds=("relationship_state",),
        entity_refs=("character-1", "character-2"),
        chapter_ids=("chapter-1",),
        max_selected=1,
        model_request=_model_request(),
    )

    assert [item.record_id for item in result.decisions] == ["record-2"]
    assert result.decisions[0].priority == "must_use"
    assert result.model == "judge-model"
    assert len(gateway.invocations) == 1
    _, invocation = gateway.invocations[0]
    assert invocation.tools == ()
    assert invocation.request.options["temperature"] == 0


@pytest.mark.asyncio
async def test_model_reranker_rejects_unknown_candidate_ids():
    gateway = _Gateway("invented-record")
    reranker = ModelBackedMemoryReranker(_model_tasks(gateway))

    with pytest.raises(ValueError, match="unknown candidate"):
        await reranker.rerank(
            query="续写",
            candidates=(_candidate("record-1", "亲密"),),
            story_kinds=("relationship_state",),
            planner_story_kinds=(),
            entity_refs=(),
            chapter_ids=(),
            max_selected=1,
            model_request=_model_request(),
        )
