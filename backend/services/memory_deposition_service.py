"""Writing-domain policy for durable memory-source delivery."""

from __future__ import annotations

import re
from typing import Any, Mapping

from application.memory_delivery import (
    MemoryDeliveryService,
    record_source_deletion,
    record_source_revision,
)
from application.memory_operations import MemoryApplicationService, memory_metadata
from services.model_settings_service import is_setting_enabled


MEMORY_INTELLIGENCE_ENABLED_KEY = "memory_intelligence_enabled"
SOURCE_CHUNK_CHARS = 16_000
REMEMBER_PATTERNS = (
    re.compile(r"(?:请|帮我)?记住[：:]\s*(.+)", re.S),
    re.compile(r"(?:以后|后续)(?:都)?(?:要)?(?:保持|遵循)[：:]\s*(.+)", re.S),
    re.compile(r"设定为[：:]\s*(.+)", re.S),
)


async def resolve_book_id_for_chapter(db: Any, chapter_id: str | None) -> str | None:
    if not chapter_id:
        return None
    row = await db.fetch_one(
        "SELECT o.book_id AS book_id FROM outline_chapters oc "
        "JOIN outlines o ON oc.outline_id = o.id WHERE oc.id = ?",
        [chapter_id],
    )
    book_id = (row or {}).get("book_id")
    return str(book_id) if book_id else None


async def resolve_book_id_for_session(db: Any, session_id: int | None) -> str | None:
    if session_id is None:
        return None
    row = await db.fetch_one(
        "SELECT book_id, chapter_id FROM ai_sessions WHERE id = ?", [session_id]
    )
    if not row:
        return None
    if row.get("book_id"):
        return str(row["book_id"])
    return await resolve_book_id_for_chapter(db, row.get("chapter_id"))


async def record_chapter_diff_candidate(
    db: Any, *, chapter_id: str, diff_id: int | None, before_text: str,
    after_text: str, source: str, accepted_segments: int,
) -> tuple[str, ...]:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return ()
    book_id = await resolve_book_id_for_chapter(db, chapter_id)
    content = _changed_text(after_text, before_text)
    if not book_id or not content or diff_id is None:
        return ()
    return await _record_chunks(
        db, book_id=book_id, source_base=f"chapter-diff:{diff_id}", text=content,
        metadata=memory_metadata(
            kind="plot", scope_type="chapter", scope_id=chapter_id,
            importance=3, confidence=0.7,
        ), inference=True,
    )


async def record_character_diff_candidate(
    db: Any, *, book_id: str, character_id: int, character_name: str,
    after_profile_md: str, source_id: int | None, source: str,
    accepted_segments: int,
) -> tuple[str, ...]:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return ()
    content = _setting_content("人物", character_name, after_profile_md)
    if not content or source_id is None:
        return ()
    return await _record_chunks(
        db, book_id=book_id,
        source_base=f"setting-diff:{source_id}:character:{character_id}",
        text=content,
        metadata=memory_metadata(
            kind="character", scope_type="character", scope_id=str(character_id),
            importance=4, confidence=0.75,
        ), inference=True,
    )


async def record_entity_diff_candidate(
    db: Any, *, book_id: str, entity_id: int, entity_name: str,
    after_profile_md: str, source_id: int | None, source: str,
    accepted_segments: int,
) -> tuple[str, ...]:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return ()
    content = _setting_content("世界设定", entity_name, after_profile_md)
    if not content or source_id is None:
        return ()
    return await _record_chunks(
        db, book_id=book_id,
        source_base=f"setting-diff:{source_id}:entity:{entity_id}", text=content,
        metadata=memory_metadata(kind="world", importance=4, confidence=0.75),
        inference=True,
    )


async def record_background_diff_candidate(
    db: Any, *, book_id: str, after_content: str, source_id: int | None,
    source: str, accepted_segments: int,
) -> tuple[str, ...]:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return ()
    content = _clean(after_content)
    if not content or source_id is None:
        return ()
    return await _record_chunks(
        db, book_id=book_id,
        source_base=f"setting-diff:{source_id}:background",
        text=f"故事背景：{content}",
        metadata=memory_metadata(kind="world", importance=4, confidence=0.75),
        inference=True,
    )


async def record_inline_article_candidate(
    db: Any, *, chapter_id: str, content: str, source: str,
) -> tuple[str, ...]:
    book_id = await resolve_book_id_for_chapter(db, chapter_id)
    if not book_id:
        return ()
    if str(source or "") != "inline_edit" or not _clean(content):
        return await record_deleted_source(
            db,
            book_id=book_id,
            source_base=f"article:{chapter_id}",
        )
    return await _record_chunks(
        db, book_id=book_id, source_base=f"article:{chapter_id}", text=content,
        metadata=memory_metadata(
            kind="plot", scope_type="chapter", scope_id=chapter_id,
            importance=3, confidence=0.65,
        ), inference=True,
    )


async def record_explicit_conversation_memory(
    db: Any, *, book_id: str | None, conversation_id: int | None, prompt: str,
) -> tuple[str, ...]:
    content = extract_explicit_memory(prompt)
    if not book_id or not content or conversation_id is None:
        return ()
    existing = await db.fetch_one(
        "SELECT 1 AS present FROM memory_source_heads "
        "WHERE book_id = ? AND source_id LIKE ? LIMIT 1",
        [book_id, f"conversation:{conversation_id}#chunk:%"],
    )
    if existing is not None:
        return ()
    return await _record_chunks(
        db, book_id=book_id, source_base=f"conversation:{conversation_id}",
        text=content,
        metadata=memory_metadata(
            kind="canon", importance=5, confidence=1.0, pinned=True
        ), inference=False, state="active",
    )


async def record_manual_character(
    db: Any, row: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if not row:
        return ()
    return await _record_chunks(
        db, book_id=str(row["book_id"]), source_base=f"character:{row['id']}",
        text=_setting_content("人物", row.get("name") or "", row.get("profile_md") or ""),
        metadata=memory_metadata(
            kind="character", scope_type="character", scope_id=str(row["id"]),
            importance=4, confidence=1.0,
        ), inference=False,
    )


async def record_manual_entity(
    db: Any, row: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if not row:
        return ()
    return await _record_chunks(
        db, book_id=str(row["book_id"]),
        source_base=f"setting-entity:{row['id']}",
        text=_setting_content(
            "世界设定", row.get("name") or "", row.get("profile_md") or ""
        ),
        metadata=memory_metadata(kind="world", importance=4, confidence=1.0),
        inference=False,
    )


async def record_manual_background(
    db: Any, book_id: str, content: str,
) -> tuple[str, ...]:
    clean = _clean(content)
    return await _record_chunks(
        db, book_id=book_id, source_base=f"story-background:{book_id}",
        text=f"故事背景：{clean}" if clean else "",
        metadata=memory_metadata(kind="world", importance=4, confidence=1.0),
        inference=False,
    )


async def record_outline_plan(
    db: Any, row: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if not row or not row.get("book_id"):
        return ()
    content = _clean(row.get("markdown_content") or "")
    return await _record_chunks(
        db, book_id=str(row["book_id"]), source_base=f"outline:{row['id']}",
        text=f"大纲计划：{content}" if content else "",
        metadata=memory_metadata(
            kind="summary", scope_type="outline", scope_id=str(row["id"]),
            keywords="计划 大纲 非既成事实", importance=3, confidence=1.0,
        ), inference=False,
    )


async def record_deleted_source(
    db: Any, *, book_id: str, source_base: str,
) -> tuple[str, ...]:
    rows = await db.fetch_all(
        "SELECT source_id FROM memory_source_heads "
        "WHERE book_id = ? AND source_id LIKE ? AND deleted = 0",
        [book_id, f"{source_base}#chunk:%"],
    )
    keys: list[str] = []
    for row in rows:
        keys.extend(await record_source_deletion(
            db, book_id=book_id, source_id=row["source_id"]
        ))
    return tuple(keys)


async def record_deleted_chapter_sources(
    db: Any,
    *,
    book_id: str,
    chapter_id: str,
) -> tuple[str, ...]:
    diff_rows = await db.fetch_all(
        "SELECT id FROM chapter_diff_history WHERE chapter_id = ?",
        [chapter_id],
    )
    source_bases = [
        f"article:{chapter_id}",
        *(f"chapter-diff:{row['id']}" for row in diff_rows),
    ]
    keys: list[str] = []
    for source_base in source_bases:
        keys.extend(await record_deleted_source(
            db,
            book_id=book_id,
            source_base=source_base,
        ))
    return tuple(keys)


async def record_deleted_conversation_sources(
    db: Any,
    conversation_ids,
) -> tuple[str, ...]:
    ids = tuple(dict.fromkeys(
        int(value) for value in conversation_ids if int(value) > 0
    ))
    keys: list[str] = []
    for conversation_id in ids:
        rows = await db.fetch_all(
            "SELECT book_id, source_id FROM memory_source_heads "
            "WHERE source_id LIKE ? AND deleted = 0",
            [f"conversation:{conversation_id}#chunk:%"],
        )
        for row in rows:
            keys.extend(await record_source_deletion(
                db,
                book_id=str(row["book_id"]),
                source_id=str(row["source_id"]),
            ))
    return tuple(keys)


async def deliver_recorded(db: Any, keys: tuple[str, ...]):
    if not keys:
        return ()
    from application.agent_composition import get_agent_composition

    composition = get_agent_composition()
    service = MemoryDeliveryService(
        db, MemoryApplicationService(db, composition.memory_resource)
    )
    return await service.deliver_many(keys)


async def _record_chunks(
    db: Any, *, book_id: str, source_base: str, text: str,
    metadata: Mapping[str, Any], inference: bool, state: str = "active",
) -> tuple[str, ...]:
    if inference and not await is_setting_enabled(
        db, MEMORY_INTELLIGENCE_ENABLED_KEY
    ):
        return await record_deleted_source(
            db,
            book_id=book_id,
            source_base=source_base,
        )
    clean = _clean(text)
    chunks = _chunks(clean)
    keys: list[str] = []
    for index, (start, end, chunk) in enumerate(chunks, start=1):
        source_id = f"{source_base}#chunk:{index:04d}"
        keys.extend(await record_source_revision(
            db, book_id=book_id, source_id=source_id, text=chunk,
            metadata={
                **dict(metadata), "sourceChunk": index, "sourceStart": start,
                "sourceEnd": end, "sourceLength": len(clean),
            },
            inference=inference, state=state,
        ))
    existing = await db.fetch_all(
        "SELECT source_id FROM memory_source_heads "
        "WHERE book_id = ? AND source_id LIKE ? AND deleted = 0",
        [book_id, f"{source_base}#chunk:%"],
    )
    live = {
        f"{source_base}#chunk:{index:04d}"
        for index in range(1, len(chunks) + 1)
    }
    for row in existing:
        if row["source_id"] not in live:
            keys.extend(await record_source_deletion(
                db, book_id=book_id, source_id=row["source_id"]
            ))
    return tuple(keys)


def extract_explicit_memory(prompt: str) -> str:
    text = str(prompt or "").strip()
    for pattern in REMEMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            return _clean(match.group(1))
    return ""


def _is_ai_source(source: str) -> bool:
    return str(source or "").lower().startswith("ai")


def _changed_text(after_text: str, before_text: str = "") -> str:
    after, before = _clean(after_text), _clean(before_text)
    return "" if not after or after == before else after


def _setting_content(label: str, name: str, profile_md: str) -> str:
    body = _clean(profile_md)
    if not body:
        return ""
    clean_name = str(name or "").strip()
    prefix = f"{label}「{clean_name}」：" if clean_name else f"{label}："
    return prefix + body


def _clean(text: Any) -> str:
    value = str(text or "").strip()
    return re.sub(r"^(请|帮我)?记住[：:\s]*", "", value).strip(" 。\n\t")


def _chunks(text: str) -> tuple[tuple[int, int, str], ...]:
    if not text:
        return ()
    result = []
    start = 0
    while start < len(text):
        hard_end = min(start + SOURCE_CHUNK_CHARS, len(text))
        end = hard_end
        if hard_end < len(text):
            boundary = max(
                text.rfind("\n\n", start, hard_end),
                text.rfind("。", start, hard_end),
            )
            if boundary > start + SOURCE_CHUNK_CHARS // 2:
                end = boundary + (0 if text.startswith("\n\n", boundary) else 1)
        chunk = text[start:end].strip()
        if chunk:
            result.append((start, end, chunk))
        start = end
    return tuple(result)


__all__ = [
    "MEMORY_INTELLIGENCE_ENABLED_KEY", "deliver_recorded",
    "extract_explicit_memory", "record_background_diff_candidate",
    "record_character_diff_candidate", "record_chapter_diff_candidate",
    "record_deleted_chapter_sources", "record_deleted_conversation_sources",
    "record_deleted_source", "record_entity_diff_candidate",
    "record_explicit_conversation_memory", "record_inline_article_candidate",
    "record_manual_background", "record_manual_character",
    "record_manual_entity", "record_outline_plan",
    "resolve_book_id_for_chapter", "resolve_book_id_for_session",
]
