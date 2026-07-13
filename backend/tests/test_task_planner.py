from __future__ import annotations

import pytest

from services.task_planner import (
    build_planner_messages,
    generate_model_task_plan,
    normalize_model_task_plan,
    should_request_task_plan,
)


class TestShouldRequestTaskPlan:
    def test_simple_ask_without_agent_tools_does_not_emit(self):
        assert should_request_task_plan(
            user_text="epub 是什么",
            book_id="book1",
            enable_agent_tools=False,
            chat_agent_mode="ask",
        ) is False

    def test_complex_book_agent_goal_emits(self):
        assert should_request_task_plan(
            user_text="帮我优化前三章节奏，并统一女主性格",
            book_id="book1",
            enable_agent_tools=True,
            chat_agent_mode="agent",
        ) is True

    def test_ask_with_agent_tools_can_emit(self):
        assert should_request_task_plan(
            user_text="帮我检查当前章节人物一致性",
            book_id="book1",
            enable_agent_tools=True,
            chat_agent_mode="ask",
        ) is True


class TestNormalizeModelTaskPlan:
    def test_model_plan_is_normalized_to_task_plan(self):
        plan = normalize_model_task_plan(
            {
                "needsTodos": True,
                "title": "优化前三章",
                "goal": "优化前三章节奏",
                "todos": [
                    {
                        "id": "read-context",
                        "title": "读取前三章",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["getChapterContent"],
                        "riskLevel": "read",
                    },
                    {
                        "id": "analyze",
                        "title": "分析节奏问题",
                        "type": "analyze",
                        "executor": "model",
                        "riskLevel": "read",
                    },
                ],
            },
            available_tool_names={"getChapterContent"},
        )

        assert plan is not None
        assert plan["status"] == "planned"
        assert [s["id"] for s in plan["steps"]] == ["read-context", "analyze"]
        assert plan["steps"][0]["suggestedTools"] == ["getChapterContent"]

    def test_needs_todos_false_returns_none(self):
        assert normalize_model_task_plan(
            {"needsTodos": False, "todos": []},
            available_tool_names={"getChapterContent"},
        ) is None

    def test_unknown_expected_tool_rejects_plan(self):
        assert normalize_model_task_plan(
            {
                "needsTodos": True,
                "title": "坏计划",
                "todos": [
                    {
                        "id": "bad-tool",
                        "title": "调用不存在工具",
                        "type": "read",
                        "executor": "tool",
                        "expectedTools": ["notARealTool"],
                    },
                    {
                        "id": "analyze",
                        "title": "分析",
                        "type": "analyze",
                        "executor": "model",
                    },
                ],
            },
            available_tool_names={"getChapterContent"},
        ) is None

    def test_write_plan_relies_on_host_approval_gate_instead_of_confirm_step(self):
        plan = normalize_model_task_plan(
            {
                "needsTodos": True,
                "title": "写入",
                "todos": [
                    {
                        "id": "write",
                        "title": "生成修改",
                        "type": "write",
                        "executor": "model",
                        "riskLevel": "write",
                    },
                    {
                        "id": "analyze",
                        "title": "分析",
                        "type": "analyze",
                        "executor": "model",
                    },
                ],
            },
            available_tool_names=set(),
        )

        assert plan is not None
        assert [step["type"] for step in plan["steps"]] == ["write", "analyze"]

    def test_tool_step_without_expected_tools_is_rejected(self):
        assert normalize_model_task_plan(
            {
                "needsTodos": True,
                "title": "缺少工具范围",
                "todos": [
                    {
                        "id": "read",
                        "title": "读取上下文",
                        "type": "read",
                        "executor": "tool",
                    },
                ],
            },
            available_tool_names={"getChapterContent"},
        ) is None

    def test_string_needs_todos_true_is_accepted(self):
        plan = normalize_model_task_plan(
            {
                "needsTodos": "true",
                "title": "单步分析",
                "steps": [
                    {
                        "id": "analyze",
                        "title": "分析请求",
                        "type": "analyze",
                        "executor": "model",
                    },
                ],
            },
            available_tool_names=set(),
        )

        assert plan is not None
        assert [step["id"] for step in plan["steps"]] == ["analyze"]

    def test_expert_executor_is_rejected(self):
        assert normalize_model_task_plan(
            {
                "needsTodos": True,
                "title": "坏计划",
                "todos": [
                    {
                        "id": "expert-step",
                        "title": "调用专家",
                        "type": "review",
                        "executor": "expert",
                        "expertRole": "review",
                    },
                    {
                        "id": "analyze",
                        "title": "分析",
                        "type": "analyze",
                        "executor": "model",
                    },
                ],
            },
            available_tool_names=set(),
        ) is None

    def test_planner_prompt_requires_json_only(self):
        messages = build_planner_messages(
            user_text="帮我优化前三章",
            chat_agent_mode="agent",
            available_tool_names=["getChapterContent"],
        )

        assert messages[0]["role"] == "system"
        assert "JSON" in messages[0]["content"]
        assert "getChapterContent" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_generate_model_task_plan_uses_model_output(self, monkeypatch):
        import services.ai_provider as ai_provider

        async def fake_create_chat_no_stream(*args, **kwargs):
            return {
                "message": {
                    "content": """
                    {
                      "needsTodos": true,
                      "title": "检查人物一致性",
                      "todos": [
                        {
                          "id": "read-character",
                          "title": "读取人物设定",
                          "type": "read",
                          "executor": "tool",
                          "expectedTools": ["getBookCharacters"],
                          "riskLevel": "read"
                        },
                        {
                          "id": "analyze-consistency",
                          "title": "分析前后一致性",
                          "type": "analyze",
                          "executor": "model",
                          "riskLevel": "read"
                        }
                      ]
                    }
                    """
                },
                "model": "fake",
            }

        monkeypatch.setattr(ai_provider, "create_chat_no_stream", fake_create_chat_no_stream)

        plan = await generate_model_task_plan(
            key="k",
            api_provider="openai",
            planner_options={"model": "m"},
            user_text="检查女主性格前后是否一致",
            chat_agent_mode="agent",
            available_tool_names={"getBookCharacters"},
        )

        assert plan is not None
        assert [s["title"] for s in plan["steps"]] == ["读取人物设定", "分析前后一致性"]

    @pytest.mark.asyncio
    async def test_generate_model_task_plan_preserves_provider_temperature(self, monkeypatch):
        import services.ai_provider as ai_provider

        captured_options = {}

        async def fake_create_chat_no_stream(key, messages, options, api_provider, signal):
            captured_options.update(options)
            return {
                "message": {
                    "content": '{"needsTodos":true,"title":"计划","todos":[{"id":"read","title":"读取","type":"read","executor":"tool","expectedTools":["getChapterContent"]},{"id":"analyze","title":"分析","type":"analyze","executor":"model"}]}'
                },
                "model": "fake",
            }

        monkeypatch.setattr(ai_provider, "create_chat_no_stream", fake_create_chat_no_stream)

        plan = await generate_model_task_plan(
            key="k",
            api_provider="openai",
            planner_options={"model": "kimi-k2.6", "temperature": 1, "max_tokens": 8192},
            user_text="帮我分析当前章节的问题",
            chat_agent_mode="agent",
            available_tool_names={"getChapterContent"},
        )

        assert plan is not None
        assert captured_options["temperature"] == 1
        assert captured_options["thinking"] == {"type": "disabled"}
        assert captured_options["max_tokens"] == 1200

    @pytest.mark.asyncio
    async def test_generate_model_task_plan_retries_original_thinking_profile(self, monkeypatch):
        import services.ai_provider as ai_provider

        captured: list[dict] = []

        async def fake_create_chat_no_stream(_key, _messages, options, _provider, _signal):
            captured.append(dict(options))
            if len(captured) == 1:
                raise RuntimeError("thinking disabled is unsupported")
            return {
                "message": {
                    "content": '{"needsTodos":false,"reason":"simple","todos":[]}'
                },
                "model": "fake",
            }

        monkeypatch.setattr(ai_provider, "create_chat_no_stream", fake_create_chat_no_stream)
        plan = await generate_model_task_plan(
            key="k",
            api_provider="openai",
            planner_options={
                "model": "provider-model",
                "temperature": 1,
                "thinking": {"type": "enabled"},
            },
            user_text="simple question",
            chat_agent_mode="agent",
            available_tool_names=set(),
        )

        assert plan is not None
        assert captured[0]["thinking"] == {"type": "disabled"}
        assert captured[1]["thinking"] == {"type": "enabled"}
        assert captured[0]["temperature"] == captured[1]["temperature"] == 1

    @pytest.mark.asyncio
    async def test_generate_model_task_plan_turns_no_todos_into_direct_answer(self, monkeypatch):
        import services.ai_provider as ai_provider

        async def fake_create_chat_no_stream(*_args, **_kwargs):
            return {
                "message": {
                    "content": '{"needsTodos":false,"reason":"simple","todos":[]}'
                },
                "model": "fake",
            }

        monkeypatch.setattr(ai_provider, "create_chat_no_stream", fake_create_chat_no_stream)
        plan = await generate_model_task_plan(
            key="k",
            api_provider="openai",
            planner_options={"model": "m"},
            user_text="explain plan versus execution",
            chat_agent_mode="agent",
            available_tool_names={"deleteCharacter"},
        )

        assert plan is not None
        assert [step["id"] for step in plan["steps"]] == ["respond"]
        assert all(not step.get("suggestedTools") for step in plan["steps"])

    @pytest.mark.asyncio
    async def test_generate_model_task_plan_rejects_invalid_model_tools(self, monkeypatch):
        import services.ai_provider as ai_provider

        async def fake_create_chat_no_stream(*args, **kwargs):
            return {
                "message": {
                    "content": '{"needsTodos":true,"title":"坏计划","todos":[{"id":"bad","title":"读","type":"read","executor":"tool","expectedTools":["fakeTool"]},{"id":"analyze","title":"分析","type":"analyze","executor":"model"}]}'
                },
                "model": "fake",
            }

        monkeypatch.setattr(ai_provider, "create_chat_no_stream", fake_create_chat_no_stream)

        plan = await generate_model_task_plan(
            key="k",
            api_provider="openai",
            planner_options={"model": "m"},
            user_text="检查女主性格前后是否一致",
            chat_agent_mode="agent",
            available_tool_names={"getBookCharacters"},
        )

        assert plan is None
