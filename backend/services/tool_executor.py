"""
Tool executor —— Agent 工具调用的 dispatch 层。

共享 helper / 注册表 / 装饰器 / ``ToolResult`` 全部下沉到
``services.tool_runtime``（无副作用叶子模块）。本模块只保留三件事：

* ``CACHE_PREDICTORS`` 各预测器 —— 提前判定工具是否会命中读缓存（前端标灰）
* ``run_tools`` / ``tool_will_hit_read_cache`` 两个对外 dispatch 入口
* 在文件末尾 import 各 ``services.tool_handlers.*``，触发它们用装饰器
  把自己注册进 ``TOOL_HANDLERS``
* ``validate_loaded_tool_contract`` 在启动时断言模型可见 schema 与运行时
  handler、缓存注册完全一致

为保持对外 import 路径不变，本模块从 ``tool_runtime`` re-export 了
``ToolResult`` / ``tool`` / ``TOOL_HANDLERS`` / ``CACHE_PREDICTORS`` 等公共符号。

依赖方向：``tool_runtime`` ← ``tool_handlers.*`` ← ``tool_executor``，无环。

新增工具 = 在某个 ``tool_handlers.*`` 里加一个 ``@tool("xxx")`` handler
（若会读缓存，再在本文件加一个 ``@cache_predictor("xxx")``）。
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from services.tool_runtime import (  # noqa: F401  (re-export 保持旧 import 路径)
    CACHE_PREDICTORS,
    CachePredictor,
    READ_CACHE_KEYS,
    TOOL_HANDLERS,
    ToolHandler,
    ToolResult,
    _ensure_chapter_content_cache,
    _err,
    _parse_args,
    _read_tool_cache_get,
    _reject_numeric_chapter_ids_for_batch,
    _runtime_writing_chapters,
    build_read_cache_key,
    cache_predictor,
    chapter_allowed_by_writing_catalog,
    chapters_allowed_by_writing_catalog,
    resolve_book_id_for_tools,
    resolve_chapter_id_strict,
    tool,
)
from services.tool_policy import TOOL_POLICIES, policy_coverage
from services.tool_security import (
    parse_tool_arguments,
    sanitize_tool_result,
    validate_book_scope,
    validate_tool_call_batch,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache predictors —— 提前判定是否会命中读缓存（用于 UI 标灰）
# ---------------------------------------------------------------------------
#
# 走 readToolCache 的工具（listWritingChapters / getBookCharacters / ... /
# listOutlines）的缓存键由 tool_runtime.READ_CACHE_KEYS 单一事实源生成，
# predictor 一律走下面这个通用判定：复用 handler 写缓存时的同一把 key，
# 从根上杜绝“两处 key 写法漂移”。
#
# 只有 getChapterContent / batchGetChapterContents 走的是 chapterContentCache
# （按 chapterId 命中、且要先过写作目录鉴权），无法用字符串 key 表达，保留专用
# predictor。


def _make_read_cache_predictor(name: str) -> CachePredictor:
    def predict(ctx: dict, args: dict) -> bool:
        key = build_read_cache_key(name, ctx, args)
        return key is not None and _read_tool_cache_get(ctx, key) is not None
    return predict


for _name in READ_CACHE_KEYS:
    CACHE_PREDICTORS[_name] = _make_read_cache_predictor(_name)


def _tool_is_authorized(ctx: dict, name: str | None) -> bool:
    """Apply the narrowest tool allow-list supplied by an orchestrator.

    ``subagentAllowedToolNames`` is kept for backward compatibility.  The
    generic name is used by the top-level task-plan executor as well.
    """
    for key in ("allowedToolNames", "subagentAllowedToolNames"):
        allowed = ctx.get(key)
        if isinstance(allowed, set):
            return not name or name in allowed
    return True


@cache_predictor("getChapterContent")
def _predict_get_chapter_content(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, ctx.get("chapterId"))
    if not resolved["ok"] or not resolved.get("chapterId"):
        return False
    cid = resolved["chapterId"]
    if not chapter_allowed_by_writing_catalog(cid, writing_chapters)["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(cache and str(cid).strip() in cache)


@cache_predictor("batchGetChapterContents")
def _predict_batch_get_chapter_contents(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    nc = _reject_numeric_chapter_ids_for_batch(args.get("chapterIds", []))
    if not nc["ok"]:
        return False
    cids = nc.get("ids", [])
    if not chapters_allowed_by_writing_catalog(cids, writing_chapters)["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(cids and all(cache and str(c).strip() in cache for c in cids))


# ---------------------------------------------------------------------------
# Handler 注册 —— 各 tool_handlers.* 模块在 import 时用装饰器登记自身
# ---------------------------------------------------------------------------

from services.tool_handlers import chapter_tools as _chapter_tools  # noqa: E402,F401
from services.tool_handlers import context_tools as _context_tools  # noqa: E402,F401
from services.tool_handlers import dashboard_tools as _dashboard_tools  # noqa: E402,F401
from services.tool_handlers import memory_tools as _memory_tools  # noqa: E402,F401
from services.tool_handlers import outline_tools as _outline_tools  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def validate_loaded_tool_contract():
    """Fail startup when model-visible and executable tool capabilities drift.

    This runs after ``tool_router`` has loaded ``SKILL.md`` files.  Keeping it
    here also guarantees all decorator-based handler registrations have been
    triggered before the comparison is made.
    """
    from services.tool_contract import inspect_tool_contract
    from services.tool_router import get_api_skill_items

    report = inspect_tool_contract(
        get_api_skill_items(),
        TOOL_HANDLERS,
        CACHE_PREDICTORS,
        READ_CACHE_KEYS,
    )
    if not report.is_valid:
        raise RuntimeError(f"Agent 工具契约校验失败：{report.describe_violations()}")
    unclassified, orphaned_policies = policy_coverage(TOOL_HANDLERS)
    if unclassified or orphaned_policies:
        parts: list[str] = []
        if unclassified:
            parts.append(f"未分类工具：{', '.join(sorted(unclassified))}")
        if orphaned_policies:
            parts.append(f"无 handler 的安全策略：{', '.join(sorted(orphaned_policies))}")
        raise RuntimeError(f"Agent 工具安全策略校验失败：{'；'.join(parts)}")
    logger.info("[agent] 工具契约校验通过：%d 个工具", report.tool_count)
    return report


def tool_will_hit_read_cache(ctx: dict, tc: dict, writing_chapters: list[dict]) -> bool:
    """提前预测一个工具调用是否会走读缓存。

    ``writing_chapters`` 参数保留是为了向后兼容 —— 实际 predictor 都直接读 ctx
    里的 ``writingChapters``，因为这两者必须一致（``run_tools`` 入口已经保证）。
    """
    name = (tc.get("function") or {}).get("name")
    args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
    if not _tool_is_authorized(ctx, name):
        return False
    predictor = CACHE_PREDICTORS.get(name or "")
    if predictor is None:
        return False
    try:
        return bool(predictor(ctx, args))
    except Exception:
        return False


async def run_tools(
    tool_calls: list[dict],
    ctx: dict,
    send_chunk: Callable[[dict], None] | None = None,
    signal=None,
) -> list[dict]:
    """批量执行 tool_calls，返回 ``[{tool_call_id, content}, ...]``。

    工具间共享状态全部走 ``ctx`` —— ``createWritingChapter`` 等会原地更新
    ``ctx['chapterId'] / ctx['writingChapters']``，本批后续工具自然能看到。
    """
    results: list[dict] = []
    batch_error = validate_tool_call_batch(tool_calls)
    if batch_error:
        content = json.dumps({"success": False, "error": batch_error}, ensure_ascii=False)
        return [
            {"tool_call_id": call.get("id"), "content": content}
            for call in tool_calls
        ]
    writing_chapters_snapshot = _runtime_writing_chapters(ctx)
    tool_read_cache_mask = [
        tool_will_hit_read_cache(ctx, tc, writing_chapters_snapshot) for tc in tool_calls
    ]
    if send_chunk and any(tool_read_cache_mask):
        send_chunk({"toolReadCacheMask": tool_read_cache_mask})

    for i, tc in enumerate(tool_calls):
        name = (tc.get("function") or {}).get("name")
        raw_args = (tc.get("function") or {}).get("arguments", "{}")
        args, argument_error = parse_tool_arguments(raw_args)
        from_cache = False

        try:
            if argument_error or args is None:
                content = json.dumps(
                    {"success": False, "error": argument_error},
                    ensure_ascii=False,
                )
            elif scope_error := validate_book_scope(ctx, args):
                content = json.dumps(
                    {"success": False, "error": scope_error},
                    ensure_ascii=False,
                )
            elif not _tool_is_authorized(ctx, name):
                content = json.dumps(
                    {"error": f"工具「{name}」不在当前执行计划的授权范围，已拒绝执行。"},
                    ensure_ascii=False,
                )
            elif not name or name not in TOOL_HANDLERS:
                content = json.dumps(
                    {"error": f"\u672a\u77e5\u5de5\u5177: {name}"},
                    ensure_ascii=False,
                )
            elif name not in TOOL_POLICIES:
                content = json.dumps(
                    {"error": f"工具「{name or '未知'}」没有安全执行策略，已拒绝执行。"},
                    ensure_ascii=False,
                )
            else:
                handler = TOOL_HANDLERS.get(name or "")
                if handler is None:
                    content = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
                else:
                    policy = TOOL_POLICIES[name]
                    if policy.requires_user_approval:
                        from services.tool_approval_service import request_tool_approval

                        approval = await request_tool_approval(
                            tool_name=name,
                            args=args,
                            policy=policy,
                            send_chunk=send_chunk,
                            signal=signal,
                        )
                        if not approval.approved:
                            content = json.dumps({
                                "success": False,
                                "approvalId": approval.approval_id,
                                "approvalStatus": approval.status,
                                "error": "该操作未获用户批准，未执行。",
                            }, ensure_ascii=False)
                        else:
                            res = await handler(ctx, args, send_chunk)
                            content = res.content
                            from_cache = res.from_cache
                    else:
                        res = await handler(ctx, args, send_chunk)
                        content = res.content
                        from_cache = res.from_cache
        except Exception as err:
            logger.exception("[agent] tool handler failed: %s", name)
            content = json.dumps({
                "success": False,
                "error": "Tool execution failed.",
                "errorType": type(err).__name__,
            }, ensure_ascii=False)

        content = sanitize_tool_result(content)

        results.append({"tool_call_id": tc.get("id"), "content": content})
        if send_chunk:
            chunk: dict = {"toolIndexCompleted": i}
            if from_cache:
                chunk["toolFromCache"] = True
            send_chunk(chunk)

    return results
