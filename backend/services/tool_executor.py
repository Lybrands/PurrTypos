"""
Tool executor —— Agent 工具调用的 dispatch 层。

共享 helper / 注册表 / 装饰器 / ``ToolResult`` 全部下沉到
``services.tool_runtime``（无副作用叶子模块）。本模块只保留三件事：

* ``CACHE_PREDICTORS`` 各预测器 —— 提前判定工具是否会命中读缓存（前端标灰）
* ``run_tools`` / ``tool_will_hit_read_cache`` 两个对外 dispatch 入口
* 在文件末尾 import 各 ``services.tool_handlers.*``，触发它们用装饰器
  把自己注册进 ``TOOL_HANDLERS``

为保持对外 import 路径不变，本模块从 ``tool_runtime`` re-export 了
``ToolResult`` / ``tool`` / ``TOOL_HANDLERS`` / ``CACHE_PREDICTORS`` 等公共符号
（``ai.py`` / ``writing_subagents.py`` / 测试仍 ``from services.tool_executor import ...``）。

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
from services.tool_handlers import memory_tools as _memory_tools  # noqa: E402,F401
from services.tool_handlers import outline_tools as _outline_tools  # noqa: E402,F401


# ---------------------------------------------------------------------------
# Public dispatch
# ---------------------------------------------------------------------------


def tool_will_hit_read_cache(ctx: dict, tc: dict, writing_chapters: list[dict]) -> bool:
    """提前预测一个工具调用是否会走读缓存。

    ``writing_chapters`` 参数保留是为了向后兼容 —— 实际 predictor 都直接读 ctx
    里的 ``writingChapters``，因为这两者必须一致（``run_tools`` 入口已经保证）。
    """
    name = (tc.get("function") or {}).get("name")
    args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
    allow = ctx.get("subagentAllowedToolNames")
    if isinstance(allow, set) and name and name not in allow:
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
) -> list[dict]:
    """批量执行 tool_calls，返回 ``[{tool_call_id, content}, ...]``。

    工具间共享状态全部走 ``ctx`` —— ``createWritingChapter`` 等会原地更新
    ``ctx['chapterId'] / ctx['writingChapters']``，本批后续工具自然能看到。
    """
    results: list[dict] = []
    writing_chapters_snapshot = _runtime_writing_chapters(ctx)
    tool_read_cache_mask = [
        tool_will_hit_read_cache(ctx, tc, writing_chapters_snapshot) for tc in tool_calls
    ]
    if send_chunk and any(tool_read_cache_mask):
        send_chunk({"toolReadCacheMask": tool_read_cache_mask})

    for i, tc in enumerate(tool_calls):
        name = (tc.get("function") or {}).get("name")
        args = _parse_args((tc.get("function") or {}).get("arguments", "{}"))
        from_cache = False

        try:
            allow = ctx.get("subagentAllowedToolNames")
            if isinstance(allow, set) and name and name not in allow:
                content = json.dumps(
                    {"error": f"工具「{name}」不在当前 subagent 授权范围，已拒绝执行。"},
                    ensure_ascii=False,
                )
            else:
                handler = TOOL_HANDLERS.get(name or "")
                if handler is None:
                    content = json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)
                else:
                    res = await handler(ctx, args, send_chunk)
                    content = res.content
                    from_cache = res.from_cache
        except Exception as err:
            content = json.dumps({"error": str(err)}, ensure_ascii=False)

        results.append({"tool_call_id": tc.get("id"), "content": content})
        if send_chunk:
            chunk: dict = {"toolIndexCompleted": i}
            if from_cache:
                chunk["toolFromCache"] = True
            send_chunk(chunk)

    return results
