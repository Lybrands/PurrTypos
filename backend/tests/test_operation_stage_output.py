import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from purra.contracts import RunCreateParams, ModelStream, ModelStreamChunk, ModelFinishReason
from purra.events import AgentEvent
from purra.errors import ContractViolationError
from purra.model_invocation import AgentModelInvocationManager, ModelInvocationContext
from application.composition_factory import create_agent_composition
from application.operation_stage_output import OperationStageOutput
from application.sse_mapping import bridge_chunk_for_effect, canonical_output_to_sse_chunk
from database.connection import DatabaseConnection
from tests.test_parent_result_window import request


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "interrupted", "canceled", "truncated"])
async def test_operation_stage_is_early_serial_public_and_not_replayed(tmp_path, outcome):
    db = DatabaseConnection(tmp_path)
    await db.init()
    composition = create_agent_composition(db)
    first_chunk, release, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []
    class Gateway:
        async def complete(self, *args, **kwargs):
            raise AssertionError("stream required")
        async def stream(self, messages, invocation, signal=None):
            facts = json.loads(messages[-1].content)["completedOperation"]
            calls.append(facts)
            async def chunks():
                if facts["number"] == 1:
                    yield ModelStreamChunk(content_delta="已核对第一部分。", reasoning_delta="PRIVATE_REASONING")
                    first_chunk.set()
                    await release.wait()
                    if outcome == "interrupted":
                        raise RuntimeError("provider interrupted")
                else:
                    second_started.set()
                    yield ModelStreamChunk(content_delta="已核对第二部分。")
                yield ModelStreamChunk(finish_reason=(ModelFinishReason.LENGTH if facts["number"] == 1 and outcome == "truncated" else ModelFinishReason.STOP))
            return ModelStream(chunks=chunks(), model="test", applied_generation_limit=invocation.max_generation_tokens)
    manager = AgentModelInvocationManager(Gateway(), output_observer=composition.output_processor,
                                         budget_repository=composition._repository)
    async def factory(run_id, runtime):
        return manager, ModelInvocationContext(run_id=run_id, turn_id="turn", context_window_tokens=65536), request().model, "default"
    reporter = OperationStageOutput(db, composition.output_processor, composition.output_repository, factory)
    owner = await composition.output_repository.begin_run_lifecycle(
        RunCreateParams(session_id=None, prompt="分析材料", mode="test", requested_run_id="root", turn_id="turn"),
        AgentEvent("run.started"))
    def context(unit):
        return SimpleNamespace(run_id="root", task=SimpleNamespace(id="task"), unit=SimpleNamespace(id=unit, attempt=1))
    stop = asyncio.Event()
    first = asyncio.create_task(reporter.report(context=context("one"), facts={"number": 1}, runtime=None, signal=stop))
    second = None
    frontend = None
    try:
        ready = asyncio.create_task(first_chunk.wait())
        try:
            done, _ = await asyncio.wait((ready, first), timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if first in done:
                await first
            assert ready in done, "Stage did not publish its first chunk"
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        assert not first.done()
        rows = await composition.output_repository.list_events("root", after_sequence=0, limit=10000)
        wire = [item for row in rows if (item := canonical_output_to_sse_chunk(row))]
        assert "PRIVATE_REASONING" not in json.dumps(wire)
        frontend = await asyncio.create_subprocess_exec("node", "--experimental-strip-types",
            str(Path(__file__).parent / "support/parent_stage_frontend.mts"),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        frontend.stdin.write((json.dumps(wire)+"\n").encode())
        await frontend.stdin.drain()
        view = json.loads(await asyncio.wait_for(frontend.stdout.readline(), 5))
        assert view["stages"]
        assert not view["state"]["runTerminal"]
        other_reporter = OperationStageOutput(db, composition.output_processor, composition.output_repository, factory)
        second = asyncio.create_task(other_reporter.report(context=context("two"), facts={"number": 2}, runtime=None))
        await asyncio.sleep(0)
        assert not second_started.is_set()
        if outcome == "canceled":
            stop.set()
        else:
            release.set()
        if outcome == "completed":
            await asyncio.wait_for(first, 5)
            await reporter.report(context=context("one"), facts={"number": 1}, runtime=None)
        else:
            with pytest.raises((Exception, asyncio.CancelledError)):
                await asyncio.wait_for(first, 5)
            with pytest.raises(ContractViolationError, match="reconciliation"):
                await reporter.report(context=context("one"), facts={"number": 1}, runtime=None)
        await asyncio.wait_for(second, 5)
        assert calls == [{"number": 1}, {"number": 2}]
        assert (await composition._repository.get("root")).status.value == "running"
        assert await db.fetch_all("SELECT id FROM ai_agent_runs") == [{"id": "root"}]
    finally:
        release.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)
        if frontend is not None:
            frontend.stdin.close()
            await asyncio.wait_for(frontend.wait(), 5)
        await composition.shutdown()
        await db.close()

def test_bridge_chunk_for_chapter_content_effect() -> None:
    chunk = bridge_chunk_for_effect("writing.chapter_content_updated", {
        "bookId": 1, "chapterId": 7, "committedRevision": "r2",
        "noop": False, "firstContent": True,
    })
    assert chunk == {
        "chapterContentUpdated": {
            "bookId": 1, "chapterId": 7, "committedRevision": "r2",
            "firstContent": True,
        },
    }
    assert bridge_chunk_for_effect("writing.chapter_content_updated", {
        "bookId": 1, "chapterId": 7, "noop": True,
    }) is None
    assert bridge_chunk_for_effect("writing.unknown_effect", {
        "chapterId": 7,
    }) is None


def test_bridge_chunk_accepts_frozen_purra_containers() -> None:
    """purra 冻结容器为 FrozenList/FrozenDict（非原生 list/dict）。"""
    from purra.json_values import FrozenDict, FrozenList

    chunk = bridge_chunk_for_effect(
        "writing.chapters_created",
        FrozenDict({
            "bookId": 1,
            "chapters": FrozenList([
                FrozenDict({"chapterId": "a1", "title": "第55章"}),
            ]),
        }),
    )
    assert chunk is not None
    assert chunk["chaptersCreated"]["chapters"][0]["title"] == "第55章"
