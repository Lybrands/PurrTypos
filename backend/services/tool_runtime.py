"""
Tool runtime —— Agent 工具共享运行时的统一入口（facade）。

实现已按职责拆到四个聚焦子模块，这里仅 re-export 以保持
``services.tool_runtime`` 这一历史导入路径稳定：

* ``tool_registry``        注册表 / 类型 / 装饰器 / 读缓存键基建
* ``tool_context``         请求级 ctx 操作（缓存、目录拒绝集合、参数解析）
* ``tool_chapter_access``  book/chapter ID 解析与写作目录鉴权
* ``tool_data_loaders``    大纲 / 章节的 DB 读取与整形
* ``tool_read_cache_keys`` 各只读工具的缓存键定义（import 即注册）

依赖方向不变：``tool_executor`` 与 ``tool_handlers.*`` 单向依赖本入口（或
具体子模块），子模块之间仅 ``tool_read_cache_keys`` → ``tool_registry`` /
``tool_chapter_access``，无环。
"""

from __future__ import annotations

from services.tool_registry import (  # noqa: F401
    CACHE_PREDICTORS,
    CachePredictor,
    READ_CACHE_KEYS,
    ReadCacheKeyBuilder,
    TOOL_HANDLERS,
    ToolHandler,
    ToolResult,
    _err,
    build_read_cache_key,
    cache_predictor,
    read_cache_key,
    tool,
)
from services.tool_context import (  # noqa: F401
    CATALOG_TOOL_FAIL_MSG,
    _catalog_reject_payload,
    _ensure_catalog_reject_set,
    _ensure_chapter_content_cache,
    _ensure_read_tool_cache,
    _invalidate_read_tool_cache,
    _json_batch_catalog_reject,
    _json_catalog_reject,
    _parse_args,
    _read_tool_cache_get,
    _read_tool_cache_set,
    _runtime_chapter_id,
    _runtime_writing_chapters,
    _title_from_writing_catalog,
)
from services.tool_chapter_access import (  # noqa: F401
    _chapter_node_allowed_by_writing_catalog,
    _normalize_chapter_parent_id,
    _reject_numeric_chapter_ids_for_batch,
    _resolve_create_writing_parent_id,
    chapter_allowed_by_writing_catalog,
    chapters_allowed_by_writing_catalog,
    resolve_book_id_for_tools,
    resolve_chapter_id_strict,
)
from services.tool_data_loaders import (  # noqa: F401
    _get_available_outlines,
    _get_global_outline,
    _list_writing_chapters,
    _load_all_outlines_for_book,
    _load_outline_with_chapters,
    _query_outline,
    _read_chapter_plain_full,
)
from services.tool_read_cache_keys import (  # noqa: F401  (import 即注册全部缓存键)
    _max_text_length,
    normalize_query_outline_ids,
)
