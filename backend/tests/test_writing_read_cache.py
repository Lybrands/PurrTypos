"""Writing read bodies remain behind explicit tool calls."""

import json

import pytest

from application.agent_composition import set_agent_composition
from application.composition_factory import create_agent_composition
from application.writing_agent_profile import build_writing_agent_profile
from database.connection import DatabaseConnection
from dependencies import set_db
from domains.writing.contracts import WritingDomainContext
from purra.contracts import AgentRunRequest, ModelRequest
from routers.ai import chat_stream
from schemas.ai import ChatStreamRequest
from tests.test_agent_composition import _collect, _fixture_model_options
from tests.test_writing_chapter_tool_boundaries import _seed_book, _lexical


@pytest.mark.asyncio
async def test_writing_read_cache_requires_an_explicit_tool_call_and_invalidates(
    tmp_path,
    monkeypatch,
):
    db = DatabaseConnection(tmp_path)
    await db.init()
    set_db(db)
    composition = create_agent_composition(db)
    set_agent_composition(composition)
    try:
        for book in ("a", "b"):
            await _seed_book(db, book_id=book, outline_id=f"outline-{book}", group_id=f"group-{book}",
                             chapter_id=f"chapter-{book}", chapter_text=f"正文-{book}")
        profile = build_writing_agent_profile(db=db)
        request = await profile.prepare_request(AgentRunRequest(
            messages=(), model=ModelRequest(provider="fixture", model="model"), tools_enabled=True,
            domain_context=WritingDomainContext(book_id="a").to_core_context(),
        ))
        state = profile.adapter.execution_state_factory.create(request)
        read = next(item for item in profile.adapter.tool_catalog.registrations()
                    if item.schema.name == "getChapterContent")
        first = await read.handler(state, {"chapterId": "chapter-a"}, None)
        again = await read.handler(state, {"chapterId": "chapter-a"}, None)
        assert first.from_cache is False
        assert again.from_cache is True

        foreign = await profile.prepare_request(AgentRunRequest(
            messages=(),
            model=request.model,
            tools_enabled=True,
            domain_context=WritingDomainContext(book_id="b").to_core_context(),
        ))
        foreign_result = await read.handler(
            profile.adapter.execution_state_factory.create(foreign),
            {"chapterId": "chapter-b"},
            None,
        )
        assert foreign_result.from_cache is False
        assert json.loads(foreign_result.content)["plainText"] == "正文-b"

        calls = []
        call_tools = []
        async def stream(_key, messages, options, _provider, signal=None):
            calls.append(messages)
            call_tools.append(tuple(
                item["function"]["name"]
                for item in options.get("tools", [])
            ))
            supplied = json.dumps(messages, ensure_ascii=False)
            assert "正文-a" not in supplied
            assert "正文-b" not in supplied
            assert not any(message["role"] == "tool" for message in messages)
            async def chunks():
                yield {"choices": [{"delta": {"content": "依据已有章节继续创作。"}, "finish_reason": "stop"}]}
            return {"applied_generation_limit": options.get("max_tokens"), "stream": chunks(), "model": "model"}

        monkeypatch.setattr("infrastructure.models.provider_router.create_chat_stream", stream)
        response = await chat_stream(ChatStreamRequest(
            messages=[{"role": "user", "content": "依据第一章继续创作"}], apiKey="fixture",
            apiProvider="openai", options=_fixture_model_options(), enableAgentTools=True,
            bookId="a", chatAgentMode="agent", contextWindow="200k",
        ))
        events = await _collect(response)
        assert events[-1]["runResult"]["status"] == "done"
        assert len(calls) == 2
        assert call_tools[0]
        assert call_tools[1] == ()
        assert not any(event.get("kind") == "operation.started"
                       and event.get("payload", {}).get("kind") == "tool" for event in events)

        await db.execute("UPDATE articles SET content = ? WHERE chapter_id = 'chapter-a'", [_lexical("新正文-a")])
        refreshed = await read.handler(state, {"chapterId": "chapter-a"}, None)
        assert refreshed.from_cache is False
        assert json.loads(refreshed.content)["plainText"] == "新正文-a"
        cached_refresh = await read.handler(
            state,
            {"chapterId": "chapter-a"},
            None,
        )
        assert cached_refresh.from_cache is True
        assert json.loads(cached_refresh.content)["plainText"] == "新正文-a"
    finally:
        await composition.shutdown()
        set_agent_composition(None)
        set_db(None)
        await db.close()
