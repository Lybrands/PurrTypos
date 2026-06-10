"""
各只读工具的读缓存键定义（``READ_CACHE_KEYS`` 的全部注册项）。

键格式即缓存协议：handler 写缓存、tool_executor 的 predictor 判命中都经由
``build_read_cache_key`` 走到这里，保证两侧永远一致。返回 ``None`` 表示该组
args 不缓存。
"""

from __future__ import annotations

from services.tool_chapter_access import resolve_book_id_for_tools
from services.tool_registry import read_cache_key


def _max_text_length(args: dict, default: int = 32000) -> int:
    v = args.get("maxTextLength")
    return v if isinstance(v, (int, float)) else default


def normalize_query_outline_ids(args: dict) -> list:
    """queryOutline 接受 ``outlineIds`` 列表或单个 ``outlineId``，统一成列表。"""
    if isinstance(args.get("outlineIds"), list):
        return args["outlineIds"]
    if args.get("outlineId") is not None and str(args["outlineId"]).strip():
        return [str(args["outlineId"]).strip()]
    return []


@read_cache_key("listWritingChapters")
def _rck_list_writing_chapters(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    return f"listWritingChapters:{bid}" if bid else None


@read_cache_key("getBookCharacters")
def _rck_get_book_characters(ctx: dict, args: dict) -> str | None:
    char_ids = args.get("characterIds")
    name_queries = args.get("names")
    filtered = (isinstance(char_ids, list) and len(char_ids) > 0) or (
        isinstance(name_queries, list) and len(name_queries) > 0
    )
    if filtered:
        return None
    bid = resolve_book_id_for_tools(ctx, args)
    return f"getBookCharacters:all:{bid}"


@read_cache_key("listBookCharacters")
def _rck_list_book_characters(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    return f"listBookCharacters:{bid}"


@read_cache_key("getStoryBackground")
def _rck_get_story_background(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    return f"getStoryBackground:{bid}"


@read_cache_key("getBookStyle")
def _rck_get_book_style(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    return f"getBookStyle:{bid}"


@read_cache_key("queryOutline")
def _rck_query_outline(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return None
    oids = normalize_query_outline_ids(args)
    if not oids:
        return None
    oid_key = ",".join(sorted(str(x) for x in oids))
    return f"queryOutline:{bid}:{oid_key}:{_max_text_length(args)}"


@read_cache_key("getGlobalOutline")
def _rck_get_global_outline(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return None
    return f"getGlobalOutline:{bid}:{_max_text_length(args)}"


@read_cache_key("listOutlines")
def _rck_list_outlines(ctx: dict, args: dict) -> str | None:
    bid = resolve_book_id_for_tools(ctx, args)
    return f"listOutlines:{bid}" if bid else None
