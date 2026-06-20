"""Create memory candidates from durable user-approved write events.

The first version is deliberately local and deterministic: it only deposits
after data has been committed, and writes AI-derived facts as pending candidates
unless the user explicitly asked the assistant to remember something.
"""

from __future__ import annotations

import re
from typing import Any

from dependencies import get_db
from services import long_term_memory_service, memory_intelligence_service


REMEMBER_PATTERNS = [
    re.compile(r"(?:请|帮我)?记住[：:]\s*(.+)", re.S),
    re.compile(r"(?:以后|后续)(?:都)?(?:要)?(?:保持|遵循)[：:]\s*(.+)", re.S),
    re.compile(r"设定为[：:]\s*(.+)", re.S),
]


async def resolve_book_id_for_chapter(db: Any, chapter_id: str | None) -> str | None:
    if not chapter_id:
        return None
    row = await db.fetch_one(
        "SELECT o.book_id AS book_id FROM outline_chapters oc "
        "JOIN outlines o ON oc.outline_id = o.id WHERE oc.id = ?",
        [chapter_id],
    )
    bid = (row or {}).get("book_id")
    return str(bid) if bid else None


async def resolve_book_id_for_session(db: Any, session_id: int | None) -> str | None:
    if session_id is None:
        return None
    row = await db.fetch_one("SELECT book_id, chapter_id FROM ai_sessions WHERE id = ?", [session_id])
    if not row:
        return None
    if row.get("book_id"):
        return str(row["book_id"])
    return await resolve_book_id_for_chapter(db, row.get("chapter_id"))


async def deposit_chapter_diff_candidate(
    db: Any,
    *,
    chapter_id: str,
    diff_id: int | None,
    before_text: str,
    after_text: str,
    source: str,
    accepted_segments: int,
) -> dict | None:
    if int(accepted_segments or 0) <= 0:
        return None
    if not _is_ai_source(source):
        return None
    book_id = await resolve_book_id_for_chapter(db, chapter_id)
    content = _candidate_text(after_text, before_text)
    if not book_id or not content:
        return None
    smart = await memory_intelligence_service.extract_and_store_candidates(
        db,
        book_id=book_id,
        source_type="chapter_diff",
        source_id=diff_id,
        source_text=content,
        scope_type="chapter",
        scope_id=chapter_id,
        default_kind="plot",
    )
    if smart:
        return smart[0]
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="plot",
        content=content,
        scope_type="chapter",
        scope_id=chapter_id,
        importance=3,
        confidence=0.7,
        status="pending",
        source_type="chapter_diff",
        source_id=diff_id,
    )


async def deposit_character_diff_candidate(
    *,
    book_id: str,
    character_id: int,
    character_name: str,
    after_profile_md: str,
    source_id: int | None,
    source: str,
    accepted_segments: int,
) -> dict | None:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return None
    content = _setting_content("人物", character_name, after_profile_md)
    if not content:
        return None
    smart = await memory_intelligence_service.extract_and_store_candidates(
        get_db(),
        book_id=book_id,
        source_type="setting_diff",
        source_id=source_id,
        source_text=content,
        scope_type="character",
        scope_id=str(character_id),
        default_kind="character",
        source_title=character_name,
    )
    if smart:
        return smart[0]
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="character",
        content=content,
        scope_type="character",
        scope_id=str(character_id),
        importance=4,
        confidence=0.75,
        status="pending",
        source_type="setting_diff",
        source_id=source_id,
    )


async def deposit_entity_diff_candidate(
    *,
    book_id: str,
    entity_id: int,
    entity_name: str,
    after_profile_md: str,
    source_id: int | None,
    source: str,
    accepted_segments: int,
) -> dict | None:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return None
    content = _setting_content("世界设定", entity_name, after_profile_md)
    if not content:
        return None
    smart = await memory_intelligence_service.extract_and_store_candidates(
        get_db(),
        book_id=book_id,
        source_type="setting_diff",
        source_id=source_id,
        source_text=content,
        scope_type="book",
        default_kind="world",
        source_title=entity_name,
    )
    if smart:
        return smart[0]
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="world",
        content=content,
        scope_type="book",
        importance=4,
        confidence=0.75,
        status="pending",
        source_type="setting_diff",
        source_id=source_id,
    )


async def deposit_background_diff_candidate(
    *,
    book_id: str,
    after_content: str,
    source_id: int | None,
    source: str,
    accepted_segments: int,
) -> dict | None:
    if int(accepted_segments or 0) <= 0 or not _is_ai_source(source):
        return None
    content = _candidate_text(after_content)
    if not content:
        return None
    smart = await memory_intelligence_service.extract_and_store_candidates(
        get_db(),
        book_id=book_id,
        source_type="setting_diff",
        source_id=source_id,
        source_text=f"故事背景：{content}",
        scope_type="book",
        default_kind="world",
        source_title="故事背景",
    )
    if smart:
        return smart[0]
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="world",
        content=f"故事背景：{content}",
        scope_type="book",
        importance=4,
        confidence=0.75,
        status="pending",
        source_type="setting_diff",
        source_id=source_id,
    )


async def deposit_explicit_memory_from_conversation(
    *,
    book_id: str | None,
    conversation_id: int | None,
    prompt: str,
) -> dict | None:
    content = extract_explicit_memory(prompt)
    if not book_id or not content:
        return None
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="canon",
        content=content,
        scope_type="book",
        importance=5,
        confidence=1.0,
        status="active",
        pinned=1,
        source_type="conversation",
        source_id=conversation_id,
    )


async def deposit_inline_article_candidate(
    db: Any,
    *,
    chapter_id: str,
    content: str,
    source: str,
) -> dict | None:
    if str(source or "") != "inline_edit":
        return None
    book_id = await resolve_book_id_for_chapter(db, chapter_id)
    candidate = _candidate_text(content)
    if not book_id or not candidate:
        return None
    smart = await memory_intelligence_service.extract_and_store_candidates(
        db,
        book_id=book_id,
        source_type="inline_edit",
        source_id=chapter_id,
        source_text=candidate,
        scope_type="chapter",
        scope_id=chapter_id,
        default_kind="plot",
    )
    if smart:
        return smart[0]
    return await long_term_memory_service.create_memory_item(
        book_id=book_id,
        kind="plot",
        content=candidate,
        scope_type="chapter",
        scope_id=chapter_id,
        importance=3,
        confidence=0.65,
        status="pending",
        source_type="article_save",
        source_id=chapter_id,
    )


async def deposit_manual_character_memory(row: dict | None) -> dict | None:
    if not row:
        return None
    content = _setting_content("人物", row.get("name") or "", row.get("profile_md") or "")
    if not content:
        return None
    return await _upsert_source_memory(
        source_type="character",
        source_id=row.get("id"),
        create_kwargs={
            "book_id": str(row["book_id"]),
            "kind": "character",
            "content": content,
            "scope_type": "character",
            "scope_id": str(row["id"]),
            "importance": 4,
            "confidence": 1.0,
            "status": "active",
            "source_type": "character",
            "source_id": row.get("id"),
            "fingerprint": f"manual:character:{row.get('id')}",
        },
        update_data={"content": content, "status": "active", "importance": 4},
    )


async def deposit_manual_background_memory(book_id: str, content: str) -> dict | None:
    candidate = _candidate_text(content)
    if not candidate:
        return None
    content_value = f"故事背景：{candidate}"
    return await _upsert_source_memory(
        source_type="story_background",
        source_id=book_id,
        create_kwargs={
            "book_id": book_id,
            "kind": "world",
            "content": content_value,
            "scope_type": "book",
            "importance": 4,
            "confidence": 1.0,
            "status": "active",
            "source_type": "story_background",
            "source_id": book_id,
            "fingerprint": f"manual:background:{book_id}",
        },
        update_data={"content": content_value, "status": "active", "importance": 4},
    )


async def deposit_manual_entity_memory(row: dict | None) -> dict | None:
    if not row:
        return None
    content = _setting_content("世界设定", row.get("name") or "", row.get("profile_md") or "")
    if not content:
        return None
    return await _upsert_source_memory(
        source_type="setting_entity",
        source_id=row.get("id"),
        create_kwargs={
            "book_id": str(row["book_id"]),
            "kind": "world",
            "content": content,
            "scope_type": "book",
            "importance": 4,
            "confidence": 1.0,
            "status": "active",
            "source_type": "setting_entity",
            "source_id": row.get("id"),
            "fingerprint": f"manual:entity:{row.get('id')}",
        },
        update_data={"content": content, "status": "active", "importance": 4},
    )


async def deposit_outline_plan_memory(row: dict | None) -> dict | None:
    if not row:
        return None
    book_id = row.get("book_id")
    content = _candidate_text(row.get("markdown_content") or "")
    if not book_id or not content:
        return None
    content_value = f"大纲计划：{content}"
    return await _upsert_source_memory(
        source_type="outline",
        source_id=row.get("id"),
        create_kwargs={
            "book_id": str(book_id),
            "kind": "summary",
            "content": content_value,
            "keywords": "计划 大纲 非既成事实",
            "scope_type": "outline",
            "scope_id": str(row.get("id")),
            "importance": 3,
            "confidence": 1.0,
            "status": "active",
            "source_type": "outline",
            "source_id": row.get("id"),
            "fingerprint": f"manual:outline:{row.get('id')}",
        },
        update_data={
            "content": content_value,
            "keywords": "计划 大纲 非既成事实",
            "status": "active",
            "importance": 3,
        },
    )


async def _upsert_source_memory(
    *,
    source_type: str,
    source_id: Any,
    create_kwargs: dict,
    update_data: dict,
) -> dict | None:
    existing = await long_term_memory_service.get_memory_items_by_source_ids(
        source_type,
        [source_id],
    )
    if existing:
        return await long_term_memory_service.update_memory_item(existing[0]["id"], update_data)
    return await long_term_memory_service.create_memory_item(**create_kwargs)


def extract_explicit_memory(prompt: str) -> str:
    text = str(prompt or "").strip()
    for pattern in REMEMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            return _clean_candidate(match.group(1))
    return ""


def _is_ai_source(source: str) -> bool:
    s = str(source or "").lower()
    return s.startswith("ai") or "subagent" in s or "expert" in s


def _candidate_text(after_text: str, before_text: str = "") -> str:
    after = _clean_candidate(after_text)
    before = _clean_candidate(before_text)
    if not after or after == before:
        return ""
    return after[:600]


def _setting_content(label: str, name: str, profile_md: str) -> str:
    body = _clean_candidate(profile_md)
    if not body:
        return ""
    prefix = f"{label}「{str(name or '').strip()}」：" if str(name or "").strip() else f"{label}："
    return (prefix + body)[:700]


def _clean_candidate(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    value = re.sub(r"^(请|帮我)?记住[：:\s]*", "", value)
    return value.strip(" 。\n\t")
