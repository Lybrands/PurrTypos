from dataclasses import replace
from types import SimpleNamespace

import pytest

from agents.novel_analysis.root_model_execution import (
    RootNovelAnalysisModelRunner,
    uses_root_model,
)
from purra.contracts import ModelRequest
from purra.model_protocol import generic_capability_snapshot


@pytest.mark.asyncio
async def test_root_unit_uses_run_bound_structured_model_task_without_child():
    calls = []

    class _Tasks:
        async def complete_structured(self, messages, call, **kwargs):
            calls.append((messages, call, kwargs))
            return SimpleNamespace(value={"findings": []})

    async def factory(context):
        assert context.run_id == "root-run"
        return _Tasks()

    model = ModelRequest(
        provider="openai",
        model="test-model",
        capability_snapshot=replace(
            generic_capability_snapshot(),
            profile_id="test:model",
            context_window_tokens=32_768,
            max_generation_tokens=4_096,
        ),
    )
    runner = RootNovelAnalysisModelRunner(
        model_request=model,
        runner_factory=factory,
    )
    context = SimpleNamespace(
        run_id="root-run",
        unit=SimpleNamespace(metadata={"executionMode": "root"}),
    )

    result = await runner.complete(
        context=context,
        instruction="分析当前范围。",
        inputs={"source": "正文"},
    )

    assert result == {"findings": []}
    assert uses_root_model(context) is True
    assert len(calls) == 1
    assert calls[0][1].request == model
    assert calls[0][2]["repair_attempts"] == 1
