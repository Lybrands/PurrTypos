"""
Agent 工具的注册表与基础类型（零内部依赖的最底层模块）。

包含：
* ``ToolResult`` / ``ToolHandler`` / ``CachePredictor`` 类型
* ``TOOL_HANDLERS`` / ``CACHE_PREDICTORS`` 注册表与 ``tool`` / ``cache_predictor`` 装饰器
* ``READ_CACHE_KEYS`` 注册表与 ``read_cache_key`` / ``build_read_cache_key``
  （读缓存键的"单一事实源"，键的具体定义见 ``tool_read_cache_keys``）
* 统一错误返回 ``_err``
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class ToolResult:
    """工具执行结果。``content`` 是要喂回给 LLM 的字符串；
    ``from_cache`` 仅用于前端流式反馈"该工具走了读缓存"。"""
    content: str
    from_cache: bool = False


ToolHandler = Callable[[dict, dict, Callable[[dict], None] | None], Awaitable[ToolResult]]
CachePredictor = Callable[[dict, dict], bool]

TOOL_HANDLERS: dict[str, ToolHandler] = {}
CACHE_PREDICTORS: dict[str, CachePredictor] = {}


def tool(name: str) -> Callable[[ToolHandler], ToolHandler]:
    def deco(fn: ToolHandler) -> ToolHandler:
        TOOL_HANDLERS[name] = fn
        return fn
    return deco


def cache_predictor(name: str) -> Callable[[CachePredictor], CachePredictor]:
    def deco(fn: CachePredictor) -> CachePredictor:
        CACHE_PREDICTORS[name] = fn
        return fn
    return deco


def _err(payload: dict | str) -> ToolResult:
    """统一错误返回。传 dict 自动 JSON，传 str 直接当成 ``error`` 字段。"""
    body = payload if isinstance(payload, dict) else {"error": str(payload)}
    return ToolResult(json.dumps(body, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Read-cache key —— 读缓存键的“单一事实源”
#
# 历史上 handler 内联 ``f"listWritingChapters:{bid}"`` 写缓存，predictor 又在
# tool_executor 里重新拼一遍同样的字符串来预测命中。两处一旦写法不一致，就会
# 出现“前端标灰说走缓存 / 实际又查了一次库”，或反过来（如 queryOutline 传单个
# outlineId 时 handler 写了缓存但 predictor 永远命中不了）。
#
# 现在 key 只在这里生成一次：handler 写缓存、predictor 判命中都调用
# ``build_read_cache_key(name, ctx, args)``。返回 ``None`` 表示“这组 args 不走
# 读缓存”（例如带过滤条件的 getBookCharacters）。
# ---------------------------------------------------------------------------

ReadCacheKeyBuilder = Callable[[dict, dict], "str | None"]
READ_CACHE_KEYS: dict[str, ReadCacheKeyBuilder] = {}


def read_cache_key(name: str) -> Callable[[ReadCacheKeyBuilder], ReadCacheKeyBuilder]:
    def deco(fn: ReadCacheKeyBuilder) -> ReadCacheKeyBuilder:
        READ_CACHE_KEYS[name] = fn
        return fn
    return deco


def build_read_cache_key(name: str, ctx: dict, args: dict) -> str | None:
    fn = READ_CACHE_KEYS.get(name)
    return fn(ctx, args) if fn else None
