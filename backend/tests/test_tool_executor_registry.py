"""
tool_executor 注册表完整性。

不试图执行任何 handler（那需要数据库 / dependencies），只验证：
1. import tool_executor 不抛
2. TOOL_HANDLERS 已注册了我们公开承诺的所有工具
3. 每个 handler 是 async 且签名 (ctx, args, send_chunk)
4. 每个 cache_predictor 都对应一个真实存在的 handler（避免"前端标灰但后端没有这工具"）
5. run_tools 对未知工具 / 越权工具 / 异常 handler 都能优雅返回结构化错误

这是对历史"if/elif 大锅饭 → 注册表"重构最便宜的回归网。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from services import tool_executor as te
from services.tool_executor import (
    CACHE_PREDICTORS,
    TOOL_HANDLERS,
    ToolResult,
    run_tools,
    tool,
    tool_will_hit_read_cache,
)
from services.tool_runtime import (
    READ_CACHE_KEYS,
    _read_tool_cache_set,
    build_read_cache_key,
)
from services.tool_router import get_api_skill_items, set_skills_path
from services.tool_policy import TOOL_POLICIES, ToolExecutionMode, ToolPolicy


BACKEND_DIR = Path(__file__).resolve().parent.parent


def _declared_tool_names() -> set[str]:
    """Load the same SKILL.md declarations that production startup uses."""
    set_skills_path(str(BACKEND_DIR / "skills"))
    return {str(item["name"]) for item in get_api_skill_items()}


# ---------------------------------------------------------------------------
# 静态检查
# ---------------------------------------------------------------------------

class TestRegistryShape:
    def test_loaded_skill_declarations_match_handlers(self):
        declared = _declared_tool_names()
        missing = declared - set(TOOL_HANDLERS)
        extra = set(TOOL_HANDLERS) - declared
        assert not missing, f"SKILL.md 声明但未注册 handler：{missing}"
        assert not extra, f"已注册 handler 但没有 SKILL.md：{extra}"

    def test_each_declared_handler_is_async_with_correct_signature(self):
        for name in _declared_tool_names():
            handler = TOOL_HANDLERS[name]
            assert inspect.iscoroutinefunction(handler), f"{name} 不是 async 函数"
            sig = inspect.signature(handler)
            params = list(sig.parameters.values())
            assert len(params) == 3, (
                f"{name} 期望 (ctx, args, send_chunk) 三参数，实际 {len(params)}"
            )

    def test_cache_predictors_point_to_real_handlers(self):
        for name in CACHE_PREDICTORS:
            assert name in TOOL_HANDLERS, (
                f"cache_predictor 注册了 {name!r} 但 TOOL_HANDLERS 中找不到对应 handler。"
                "前端会因此显示'走缓存'但后端实际无该工具。"
            )

    def test_read_cache_predictors_exist_for_all_cacheable_tools(self):
        cacheable = set(READ_CACHE_KEYS) | {
            "getChapterContent", "batchGetChapterContents",
        }
        missing = cacheable - set(CACHE_PREDICTORS)
        assert not missing, f"读缓存工具缺少 predictor，前端无法提前标灰：{missing}"


# ---------------------------------------------------------------------------
# 装饰器机制
# ---------------------------------------------------------------------------

class TestToolDecorator:
    def test_tool_decorator_registers_into_global(self):
        sentinel_name = "__test_sentinel_tool__"

        @tool(sentinel_name)
        async def _h(ctx, args, send_chunk):  # pragma: no cover
            return ToolResult("ok")

        try:
            assert TOOL_HANDLERS.get(sentinel_name) is _h
        finally:
            TOOL_HANDLERS.pop(sentinel_name, None)


# ---------------------------------------------------------------------------
# run_tools 失败路径
# ---------------------------------------------------------------------------

class TestRunToolsErrorPaths:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        results = await run_tools(
            [{"id": "call_1", "function": {"name": "doesNotExist", "arguments": "{}"}}],
            ctx={},
        )
        assert len(results) == 1
        assert results[0]["tool_call_id"] == "call_1"
        body = json.loads(results[0]["content"])
        assert "未知工具" in body["error"]

    @pytest.mark.asyncio
    async def test_subagent_whitelist_rejects_unauthorized_tool(self):
        ctx = {"subagentAllowedToolNames": {"listWritingChapters"}}
        results = await run_tools(
            [{"id": "c1", "function": {"name": "getChapterContent", "arguments": "{}"}}],
            ctx=ctx,
        )
        body = json.loads(results[0]["content"])
        assert "授权" in body["error"]

    @pytest.mark.asyncio
    async def test_plan_allowlist_rejects_unplanned_tool(self):
        results = await run_tools(
            [{"id": "c1", "function": {"name": "getChapterContent", "arguments": "{}"}}],
            ctx={"allowedToolNames": set()},
        )
        body = json.loads(results[0]["content"])
        assert "执行计划" in body["error"]

    @pytest.mark.asyncio
    async def test_handler_exception_is_caught(self):
        async def _boom(ctx, args, send_chunk):
            raise RuntimeError("kaboom")

        TOOL_HANDLERS["__boom__"] = _boom
        TOOL_POLICIES["__boom__"] = ToolPolicy(ToolExecutionMode.READ, "test boom")
        try:
            results = await run_tools(
                [{"id": "c1", "function": {"name": "__boom__", "arguments": "{}"}}],
                ctx={},
            )
            body = json.loads(results[0]["content"])
            assert body["error"] == "Tool execution failed."
            assert body["errorType"] == "RuntimeError"
        finally:
            TOOL_HANDLERS.pop("__boom__", None)
            TOOL_POLICIES.pop("__boom__", None)

    @pytest.mark.asyncio
    async def test_invalid_args_json_does_not_crash(self):
        calls: list[dict] = []

        async def _record(ctx, args, send_chunk):
            calls.append(args)
            return ToolResult("ok")

        TOOL_HANDLERS["__rec__"] = _record
        TOOL_POLICIES["__rec__"] = ToolPolicy(ToolExecutionMode.READ, "test record")
        try:
            results = await run_tools(
                [{"id": "c1", "function": {"name": "__rec__", "arguments": "}{ not json"}}],
                ctx={},
            )
            body = json.loads(results[0]["content"])
            assert calls == []
            assert body["error"] == "Tool arguments are not valid JSON."
        finally:
            TOOL_HANDLERS.pop("__rec__", None)
            TOOL_POLICIES.pop("__rec__", None)

    @pytest.mark.asyncio
    async def test_send_chunk_emits_completion_per_call(self):
        async def _ok(ctx, args, send_chunk):
            return ToolResult("ok")

        TOOL_HANDLERS["__ok__"] = _ok
        TOOL_POLICIES["__ok__"] = ToolPolicy(ToolExecutionMode.READ, "test ok")
        chunks: list[dict] = []
        try:
            await run_tools(
                [
                    {"id": "c1", "function": {"name": "__ok__", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "__ok__", "arguments": "{}"}},
                ],
                ctx={},
                send_chunk=chunks.append,
            )
            indices = [c.get("toolIndexCompleted") for c in chunks if "toolIndexCompleted" in c]
            assert indices == [0, 1]
        finally:
            TOOL_HANDLERS.pop("__ok__", None)
            TOOL_POLICIES.pop("__ok__", None)


# ---------------------------------------------------------------------------
# tool_will_hit_read_cache
# ---------------------------------------------------------------------------

class TestToolWillHitReadCache:
    def test_unknown_tool_predictor_returns_false(self):
        assert tool_will_hit_read_cache(
            ctx={},
            tc={"function": {"name": "doesNotExist", "arguments": "{}"}},
            writing_chapters=[],
        ) is False

    def test_subagent_whitelist_blocks_predictor(self):
        # 不在白名单 → 直接 False，根本不会调 predictor
        assert tool_will_hit_read_cache(
            ctx={"subagentAllowedToolNames": {"foo"}, "bookId": "b1"},
            tc={"function": {"name": "listBookCharacters", "arguments": "{}"}},
            writing_chapters=[],
        ) is False

    def test_predictor_exception_returns_false_safely(self):
        from services import tool_executor as mod

        def _bad(ctx, args):
            raise RuntimeError("predictor explode")

        mod.CACHE_PREDICTORS["__bad_pred__"] = _bad
        mod.TOOL_HANDLERS["__bad_pred__"] = lambda *_a, **_kw: None  # 占位
        try:
            assert tool_will_hit_read_cache(
                ctx={},
                tc={"function": {"name": "__bad_pred__", "arguments": "{}"}},
                writing_chapters=[],
            ) is False
        finally:
            mod.CACHE_PREDICTORS.pop("__bad_pred__", None)
            mod.TOOL_HANDLERS.pop("__bad_pred__", None)


# ---------------------------------------------------------------------------
# 模块级 sanity：import 不应有循环 / 语法错误
# ---------------------------------------------------------------------------

def test_module_imports_clean():
    assert te.run_tools is run_tools
    assert isinstance(TOOL_HANDLERS, dict)
    assert isinstance(CACHE_PREDICTORS, dict)


def test_no_event_loop_assumptions_at_import():
    # 不应该在 import 时启动 loop
    assert asyncio.get_event_loop_policy() is not None


# ---------------------------------------------------------------------------
# 读缓存键单一事实源
#
# handler 写缓存与 predictor 判命中共用 build_read_cache_key，二者再也不会漂移。
# 用 builder 生成的 key 预热读缓存后，predictor 必须预测命中。
# ---------------------------------------------------------------------------

# (name, args) —— 每个走 readToolCache 的工具一组“可缓存”样例
_CACHEABLE_CASES = [
    ("listWritingChapters", {"bookId": "b1"}),
    ("getBookCharacters", {"bookId": "b1"}),
    ("listBookCharacters", {"bookId": "b1"}),
    ("getStoryBackground", {"bookId": "b1"}),
    ("getBookStyle", {"bookId": "b1"}),
    ("queryOutline", {"bookId": "b1", "outlineIds": ["o1", "o2"]}),
    ("getGlobalOutline", {"bookId": "b1"}),
    ("listOutlines", {"bookId": "b1"}),
]


class TestReadCacheKeySingleSource:
    def test_every_read_cache_key_has_handler_and_predictor(self):
        for name in READ_CACHE_KEYS:
            assert name in TOOL_HANDLERS, name
            assert name in CACHE_PREDICTORS, name

    @pytest.mark.parametrize("name,args", _CACHEABLE_CASES)
    def test_predictor_hits_cache_warmed_via_builder_key(self, name: str, args: dict):
        ctx = {"bookId": "b1"}
        tc = {"function": {"name": name, "arguments": json.dumps(args)}}
        # 未预热 → 不命中
        assert tool_will_hit_read_cache(ctx, tc, []) is False, name
        key = build_read_cache_key(name, ctx, args)
        assert key is not None, name
        _read_tool_cache_set(ctx, key, "warmed")
        # 用 builder 的 key 预热后 → predictor 必须命中（证明二者同源）
        assert tool_will_hit_read_cache(ctx, tc, []) is True, name

    def test_query_outline_single_outline_id_predicts_hit(self):
        # 回归：handler 接受单个 outlineId，旧 predictor 只看 outlineIds → 永远 miss。
        ctx = {"bookId": "b1"}
        args = {"bookId": "b1", "outlineId": "o1"}
        key = build_read_cache_key("queryOutline", ctx, args)
        assert key is not None
        _read_tool_cache_set(ctx, key, "warmed")
        tc = {"function": {"name": "queryOutline", "arguments": json.dumps(args)}}
        assert tool_will_hit_read_cache(ctx, tc, []) is True

    def test_filtered_get_book_characters_not_cached(self):
        # 带过滤条件 → 不走读缓存，builder 返回 None
        ctx = {"bookId": "b1"}
        assert build_read_cache_key("getBookCharacters", ctx, {"bookId": "b1", "names": ["x"]}) is None
        assert build_read_cache_key("getBookCharacters", ctx, {"bookId": "b1", "characterIds": [1]}) is None
