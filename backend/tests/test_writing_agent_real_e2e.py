"""Paid release gate for the real Writing Agent dynamic-planning path.

This test intentionally skips with a release-blocker message when the live
provider cannot be reached. Fake gateways never count as this gate.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from time import time

import pytest
import pytest_asyncio

from application.agent_composition import set_agent_composition
from application.agent_run_service import AgentRunService
from application.composition_factory import create_agent_composition
from application.request_mapping import to_writing_agent_request
from database.connection import DatabaseConnection
from dependencies import clear_db, set_db
from infrastructure.models.provider_model_gateway import ProviderModelGateway
from purra.contracts import (
    AgentMessage,
    AgentRunResult,
    MessageRole,
    ModelInvocation,
    ReasoningMode,
    RunStatus,
)
from purra.model_protocol import resolve_invocation_output_limit
from routers.ai import cancel_agent_run
from schemas.ai import ChatStreamRequest


_KEY_ENV = "DEEPSEEK_API_KEY"
_MODEL = "deepseek-v4-flash"
_PROFILE = "deepseek:deepseek-v4-flash"
_BASE_URL = "https://api.deepseek.com/v1"


def _lexical(text: str) -> str:
    return json.dumps({
        "root": {
            "children": [{
                "type": "paragraph",
                "children": [{"type": "text", "text": text}],
            }],
        },
    }, ensure_ascii=False)


def _provider_options() -> dict[str, object]:
    return {
        "model": _MODEL,
        "model_profile": _PROFILE,
        "baseURL": _BASE_URL,
        "max_tokens": 2_048,
        "thinking": {"type": "disabled"},
    }


def _request(prompt: str) -> ChatStreamRequest:
    return ChatStreamRequest(
        messages=[{"role": "user", "content": prompt}],
        apiKey="provided-by-test-at-runtime",
        apiProvider="openai",
        baseURL=_BASE_URL,
        options={
            "model": _MODEL,
            "model_profile": _PROFILE,
            "max_tokens": 2_048,
            "thinking": {"type": "disabled"},
        },
        enableAgentTools=True,
        bookId="book-live-writing",
        chapterId="chapter-live-writing",
        currentChapterTitle="第一章：清河弄堂",
        chatAgentMode="agent",
        contextWindow="200k",
    )


def _api_key_or_skip() -> str:
    if _KEY_ENV not in os.environ or not os.environ[_KEY_ENV].strip():
        pytest.skip(
            f"RELEASE BLOCKER: {_KEY_ENV} is missing; "
            "Writing Agent dynamic-planning E2E was not executed"
        )
    return os.environ[_KEY_ENV]


@pytest_asyncio.fixture
async def live_writing_runtime(tmp_path: Path):
    db = DatabaseConnection(tmp_path)
    await db.init()
    await db.execute(
        "INSERT INTO books (id, title) VALUES (?, ?)",
        ["book-live-writing", "真实 Provider 小说验收书"],
    )
    await db.execute(
        "INSERT INTO outlines (id, title, type, book_id) "
        "VALUES (?, ?, 'writing', ?)",
        ["outline-live-writing", "写作目录", "book-live-writing"],
    )
    await db.execute(
        "INSERT INTO outline_chapters "
        "(id, outline_id, title, level, sort) VALUES (?, ?, ?, 1, 1)",
        [
            "chapter-live-writing",
            "outline-live-writing",
            "第一章：清河弄堂",
        ],
    )
    original_article = _lexical(
        "傍晚的清河弄堂很安静。屋檐下那枚旧铜风铃只在穿堂风来时轻响，"
        "声音贴着青砖墙慢慢远去。"
    )
    await db.execute(
        "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
        ["chapter-live-writing", original_article],
    )
    set_db(db)
    composition = create_agent_composition(db)
    set_agent_composition(composition)
    try:
        yield db, composition, original_article
    finally:
        await composition.shutdown()
        set_agent_composition(None)
        clear_db(db)
        await db.close()


async def _assert_provider_available(api_key: str) -> None:
    body = _request("只回复：可用")
    request = to_writing_agent_request(body, _provider_options())
    invocation = ModelInvocation(
        request=request.model,
        output_limit=resolve_invocation_output_limit(
            request.model.capability_snapshot,
            explicit_user_override=64,
        ),
        reasoning_mode=ReasoningMode.DISABLED,
    )
    try:
        completion = await asyncio.wait_for(
            ProviderModelGateway(api_key).complete(
                (AgentMessage(role=MessageRole.USER, content="只回复：可用"),),
                invocation,
            ),
            timeout=45,
        )
    except Exception as error:
        pytest.skip(
            "RELEASE BLOCKER: live Writing Agent provider is unavailable "
            f"({type(error).__name__}); dynamic-planning E2E was not executed"
        )
    assert str(completion.message.content or "").strip()


async def _run_to_terminal(
    composition,
    *,
    body: ChatStreamRequest,
    api_key: str,
    signal: asyncio.Event,
) -> AgentRunResult:
    terminal: AgentRunResult | None = None
    stream = AgentRunService(composition).run(
        body=body,
        api_key=api_key,
        provider_options=_provider_options(),
        signal=signal,
        enable_delegation=False,
    )
    async for update in stream:
        if isinstance(update, AgentRunResult):
            terminal = update
    assert terminal is not None
    return terminal


async def _wait_for_live_root_lease(
    db: DatabaseConnection,
) -> dict[str, object]:
    for _ in range(500):
        row = await db.fetch_one(
            "SELECT id, execution_owner_id, lease_expires_at_ms "
            "FROM ai_agent_runs WHERE parent_run_id IS NULL "
            "AND status = 'running' AND execution_owner_id IS NOT NULL "
            "AND lease_expires_at_ms > ? "
            "ORDER BY create_time DESC LIMIT 1",
            [int(time() * 1_000)],
        )
        if row is not None:
            return row
        await asyncio.sleep(0.01)
    raise AssertionError("Writing Agent did not persist a live Root lease")


@pytest.mark.real_provider
@pytest.mark.asyncio
async def test_live_writing_agent_replans_after_read_and_cancels_cleanly(
    live_writing_runtime,
):
    db, composition, original_article = live_writing_runtime
    api_key = _api_key_or_skip()
    await _assert_provider_available(api_key)

    prompt = "深化弄堂氛围，保留原事件并给出可应用的章节候选稿。"
    terminal = await asyncio.wait_for(
        _run_to_terminal(
            composition,
            body=_request(prompt),
            api_key=api_key,
            signal=asyncio.Event(),
        ),
        timeout=240,
    )
    assert terminal.status is RunStatus.DONE

    runs = await db.fetch_all(
        "SELECT id, status, parent_run_id, root_run_id FROM ai_agent_runs "
        "ORDER BY create_time ASC"
    )
    assert runs == [{
        "id": terminal.run_id,
        "status": "done",
        "parent_run_id": None,
        "root_run_id": terminal.run_id,
    }]
    event_rows = await db.fetch_all(
        "SELECT id, event_type, payload_json FROM ai_agent_run_events "
        "WHERE run_id = ? ORDER BY id ASC",
        [terminal.run_id],
    )
    events = [
        (row["event_type"], json.loads(row["payload_json"] or "{}"))
        for row in event_rows
    ]
    planning_call_index = next(
        index
        for index, (event_type, payload) in enumerate(events)
        if event_type == "model.call_recorded"
        and payload.get("phase") == "planning"
    )
    plan_events = [
        (index, payload)
        for index, (event_type, payload) in enumerate(events)
        if event_type == "run.todos_updated"
    ]
    assert len(plan_events) >= 2
    assert planning_call_index < plan_events[0][0]

    initial_plan_title = str(plan_events[0][1].get("title") or "")
    assert any(marker in initial_plan_title for marker in ("弄堂", "氛围"))
    assert initial_plan_title not in {
        "读取所需内容",
        "生成内容",
        "检查并回复",
        "执行任务",
    }

    read_receipts = await db.fetch_all(
        "SELECT tool_call_id, tool_name, content, error_code "
        "FROM ai_agent_tool_receipts WHERE run_id = ? "
        "AND tool_name = 'getChapterContent' ORDER BY create_time ASC",
        [terminal.run_id],
    )
    assert len(read_receipts) == 1
    read_receipt = read_receipts[0]
    assert read_receipt["error_code"] is None
    assert "旧铜风铃" in read_receipt["content"]
    read_call_id = str(read_receipt["tool_call_id"])
    read_completed_index = next(
        index
        for index, (event_type, payload) in enumerate(events)
        if event_type == "tool.call_completed"
        and payload.get("toolCallId") == read_call_id
        and payload.get("toolName") == read_receipt["tool_name"]
        and payload.get("outcome") in {"completed", "progressed"}
    )
    replanning_call_index = next(
        index
        for index, (event_type, payload) in enumerate(events)
        if index > read_completed_index
        and event_type == "model.call_recorded"
        and payload.get("phase") == "replanning"
    )
    revised_plan_index = next(
        index
        for index, payload in plan_events
        if index > replanning_call_index
    )
    assert (
        plan_events[0][0]
        < read_completed_index
        < replanning_call_index
        < revised_plan_index
    )

    completed_ids = {
        step["id"]
        for step in plan_events[-1][1]["steps"]
        if step["status"] == "done"
    }
    initial_unfinished_after_observation = {
        (step["id"], step["title"])
        for step in plan_events[0][1]["steps"]
        if step["status"] in {"pending", "running"}
        and step["id"] not in completed_ids
    }
    revised_pending = {
        (step["id"], step["title"])
        for step in plan_events[-1][1]["steps"]
        if step["status"] in {"pending", "running"}
    }
    assert completed_ids
    assert initial_unfinished_after_observation != revised_pending
    assert any(
        marker in title
        for _, title in revised_pending
        for marker in ("铜", "风铃", "声音")
    )

    encoded_plans = json.dumps(
        [payload for _, payload in plan_events],
        ensure_ascii=False,
    ).lower()
    assert all(
        forbidden not in encoded_plans
        for forbidden in ("longtask", "long_task", "recipe")
    )
    assert not any(
        event_type.startswith("long_task.") for event_type, _ in events
    )

    tool_receipts = await db.fetch_all(
        "SELECT tool_call_id, tool_name, effects_json "
        "FROM ai_agent_tool_receipts "
        "WHERE run_id = ? ORDER BY create_time ASC, tool_call_id ASC",
        [terminal.run_id],
    )
    assert "getChapterContent" in {
        row["tool_name"] for row in tool_receipts
    }
    candidate_receipts = [
        row
        for row in tool_receipts
        if row["tool_name"] == "editChapterContent"
        and any(
            effect.get("type") == "writing.proposed_chapter_diff"
            for effect in json.loads(row["effects_json"] or "[]")
        )
    ]
    assert len(candidate_receipts) == 1
    assert await db.fetch_one(
        "SELECT content FROM articles WHERE chapter_id = ?",
        ["chapter-live-writing"],
    ) == {"content": original_article}

    cancel_task = asyncio.create_task(_run_to_terminal(
        composition,
        body=_request("深化当前章节的氛围，并给出一版候选改写。"),
        api_key=api_key,
        signal=asyncio.Event(),
    ))
    live_lease = await _wait_for_live_root_lease(db)
    canceled_root_id = str(live_lease["id"])
    assert str(live_lease["execution_owner_id"] or "").strip()
    assert int(live_lease["lease_expires_at_ms"]) > int(time() * 1_000)
    cancel_response = await cancel_agent_run(canceled_root_id)
    assert cancel_response["success"] is True
    assert cancel_response["data"]["status"] == "cancel_requested"
    assert cancel_response["data"]["newlyRequested"] is True
    assert cancel_response["data"]["terminalized"] is False
    canceled = await asyncio.wait_for(cancel_task, timeout=60)
    assert canceled.run_id == canceled_root_id
    assert canceled.status is RunStatus.CANCELED
    scoped_runs = await db.fetch_all(
        "SELECT id, status, execution_owner_id, lease_expires_at_ms "
        "FROM ai_agent_runs WHERE id = ? OR root_run_id = ?",
        [canceled_root_id, canceled_root_id],
    )
    assert scoped_runs
    assert all(row["status"] != "running" for row in scoped_runs)
    assert all(row["execution_owner_id"] is None for row in scoped_runs)
    assert all(row["lease_expires_at_ms"] is None for row in scoped_runs)
