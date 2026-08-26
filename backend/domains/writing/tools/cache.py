"""Explicit, immutable cache protocol for Writing-domain tools."""

from __future__ import annotations

from types import MappingProxyType

from domains.writing.tools.contracts import CachePredictor, ReadCacheKeyBuilder
from domains.writing.tools.scope import (
    _reject_numeric_chapter_ids_for_batch,
    chapter_allowed_by_writing_catalog,
    chapters_allowed_by_writing_catalog,
    resolve_book_id_for_tools,
    resolve_chapter_id_strict,
)
from domains.writing.tools.state import (
    _ensure_chapter_content_cache,
    _read_tool_cache_get,
    _runtime_writing_chapters,
)


def _max_text_length(args: dict, default: int = 32000) -> int | float:
    value = args.get("maxTextLength")
    return value if isinstance(value, (int, float)) else default


def normalize_query_outline_ids(args: dict) -> list:
    """Normalize queryOutline's plural and singular ID arguments."""

    if isinstance(args.get("outlineIds"), list):
        return args["outlineIds"]
    if args.get("outlineId") is not None and str(args["outlineId"]).strip():
        return [str(args["outlineId"]).strip()]
    return []


def _rck_list_writing_chapters(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"listWritingChapters:{book_id}" if book_id else None


def _rck_get_book_characters(ctx: dict, args: dict) -> str | None:
    character_ids = args.get("characterIds")
    names = args.get("names")
    filtered = (isinstance(character_ids, list) and bool(character_ids)) or (
        isinstance(names, list) and bool(names)
    )
    if filtered:
        return None
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"getBookCharacters:all:{book_id}"


def _rck_list_book_characters(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"listBookCharacters:{book_id}"


def _rck_get_story_background(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"getStoryBackground:{book_id}"


def _rck_list_setting_entities(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"listSettingEntities:{book_id}" if book_id else None


def _rck_get_setting_entities(ctx: dict, args: dict) -> str | None:
    entity_ids = args.get("entityIds")
    names = args.get("names")
    entity_type = str(args.get("entityType") or "").strip()
    filtered = (isinstance(entity_ids, list) and bool(entity_ids)) or (
        isinstance(names, list) and bool(names)
    ) or bool(entity_type)
    if filtered:
        return None
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"getSettingEntities:all:{book_id}"


def _rck_get_story_health_dashboard(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"getStoryHealthDashboard:{book_id}" if book_id else None


def _rck_get_writing_stats_dashboard(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"getWritingStatsDashboard:{book_id}" if book_id else None


def _rck_query_outline(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return None
    outline_ids = normalize_query_outline_ids(args)
    if not outline_ids:
        return None
    outline_key = ",".join(sorted(str(outline_id) for outline_id in outline_ids))
    return f"queryOutline:{book_id}:{outline_key}:{_max_text_length(args)}"


def _rck_get_global_outline(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return None
    return f"getGlobalOutline:{book_id}:{_max_text_length(args)}"


def _rck_list_outlines(ctx: dict, args: dict) -> str | None:
    book_id = resolve_book_id_for_tools(ctx, args)
    return f"listOutlines:{book_id}" if book_id else None


WRITING_READ_CACHE_KEY_BUILDERS = MappingProxyType({
    "listWritingChapters": _rck_list_writing_chapters,
    "getBookCharacters": _rck_get_book_characters,
    "listBookCharacters": _rck_list_book_characters,
    "getStoryBackground": _rck_get_story_background,
    "listSettingEntities": _rck_list_setting_entities,
    "getSettingEntities": _rck_get_setting_entities,
    "getStoryHealthDashboard": _rck_get_story_health_dashboard,
    "getWritingStatsDashboard": _rck_get_writing_stats_dashboard,
    "queryOutline": _rck_query_outline,
    "getGlobalOutline": _rck_get_global_outline,
    "listOutlines": _rck_list_outlines,
})


def build_read_cache_key(name: str, ctx: dict, args: dict) -> str | None:
    builder = WRITING_READ_CACHE_KEY_BUILDERS.get(name)
    return builder(ctx, args) if builder else None


def _make_read_cache_probe(name: str) -> CachePredictor:
    def _predict(ctx: dict, args: dict) -> bool:
        key = build_read_cache_key(name, ctx, args)
        return key is not None and _read_tool_cache_get(ctx, key) is not None

    return _predict


def _predict_get_chapter_content(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(
        args,
        writing_chapters,
        ctx.get("chapterId"),
    )
    if not resolved["ok"] or not resolved.get("chapterId"):
        return False
    chapter_id = resolved["chapterId"]
    if not chapter_allowed_by_writing_catalog(
        chapter_id,
        writing_chapters,
    )["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(cache and str(chapter_id).strip() in cache)


def _predict_batch_get_chapter_contents(ctx: dict, args: dict) -> bool:
    writing_chapters = _runtime_writing_chapters(ctx)
    normalized = _reject_numeric_chapter_ids_for_batch(
        args.get("chapterIds", []),
    )
    if not normalized["ok"]:
        return False
    chapter_ids = normalized.get("ids", [])
    if not chapters_allowed_by_writing_catalog(
        chapter_ids,
        writing_chapters,
    )["ok"]:
        return False
    cache = _ensure_chapter_content_cache(ctx)
    return bool(
        chapter_ids
        and all(
            cache and str(chapter_id).strip() in cache
            for chapter_id in chapter_ids
        )
    )


WRITING_CACHE_PROBES = MappingProxyType({
    **{
        name: _make_read_cache_probe(name)
        for name in WRITING_READ_CACHE_KEY_BUILDERS
    },
    "getChapterContent": _predict_get_chapter_content,
    "batchGetChapterContents": _predict_batch_get_chapter_contents,
})


__all__ = [
    "WRITING_CACHE_PROBES",
    "WRITING_READ_CACHE_KEY_BUILDERS",
    "build_read_cache_key",
    "normalize_query_outline_ids",
]
