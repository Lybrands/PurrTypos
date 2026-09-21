"""Authoritative chapter read and mutation model for the replacement Agent."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from agents.writing.read_model import WritingReadScope
from purra.cancellation import raise_if_stopped
from utils.book_structure import count_words
from utils.text import extract_text_from_lexical


class WritingChapterMutationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class SqliteWritingChapterRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def read(
        self,
        scope: WritingReadScope,
        *,
        max_text_length: int = 48_000,
    ) -> dict[str, Any]:
        chapter = await self._require_chapter(scope)
        maximum = _bounded_text_length(max_text_length)
        stored = str(chapter.get("content") or "")
        plain = extract_text_from_lexical(stored)
        returned = plain[:maximum]
        return {
            "schemaVersion": 1,
            "bookId": scope.book_id,
            "chapterId": scope.chapter_id,
            "title": str(chapter.get("title") or ""),
            "content": returned,
            "hasContent": bool(plain.strip()),
            "articleExists": bool(chapter.get("article_id")),
            "truncated": len(plain) > len(returned),
            "returnedCharacters": len(returned),
            "totalCharacters": len(plain),
            "baseRevision": _content_revision(stored),
        }

    async def validate_edit(
        self,
        scope: WritingReadScope,
        *,
        content: str,
        base_revision: str,
        clear_content: bool,
        chapter_id: str | None = None,
        created_ids: tuple[str, ...] | frozenset[str] = (),
    ) -> None:
        _validate_edit_arguments(
            content=content,
            base_revision=base_revision,
            clear_content=clear_content,
        )
        chapter = await self._require_chapter(
            scope, chapter_id=chapter_id, created_ids=created_ids,
        )
        actual = _content_revision(str(chapter.get("content") or ""))
        desired = _content_revision(content)
        if actual not in {base_revision, desired}:
            raise WritingChapterMutationError(
                "writing_chapter_revision_conflict",
                "Chapter content changed after it was read",
            )

    async def commit_edit(
        self,
        scope: WritingReadScope,
        *,
        content: str,
        base_revision: str,
        clear_content: bool,
        chapter_id: str | None = None,
        created_ids: tuple[str, ...] | frozenset[str] = (),
        signal=None,
    ) -> dict[str, Any]:
        _validate_edit_arguments(
            content=content,
            base_revision=base_revision,
            clear_content=clear_content,
        )
        raise_if_stopped(signal)
        async with self._db.transaction(
            cancellation_linearizable=not self._db.current_task_owns_transaction()
        ):
            chapter = await self._require_chapter(
                scope, chapter_id=chapter_id, created_ids=created_ids,
            )
            target_chapter_id = str(chapter["id"])
            previous = str(chapter.get("content") or "")
            previous_revision = _content_revision(previous)
            desired_revision = _content_revision(content)

            # An exact replay after a committed-but-unacknowledged attempt is
            # successful and does not perform a second write.
            first_content = not str(previous or "").strip()
            if previous_revision == desired_revision:
                return _commit_receipt(
                    scope,
                    chapter_id=target_chapter_id,
                    previous_revision=base_revision,
                    committed_revision=desired_revision,
                    content=content,
                    noop=True,
                    first_content=first_content,
                )
            if previous_revision != base_revision:
                raise WritingChapterMutationError(
                    "writing_chapter_revision_conflict",
                    "Chapter content changed after approval was requested",
                )

            raise_if_stopped(signal)
            if chapter.get("article_id"):
                await self._db.execute(
                    "UPDATE articles SET content = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE chapter_id = ?",
                    [content, target_chapter_id],
                )
            else:
                await self._db.execute(
                    "INSERT INTO articles (chapter_id, content) VALUES (?, ?)",
                    [target_chapter_id, content],
                )
            await self._record_word_delta(
                scope.book_id,
                previous=previous,
                current=content,
            )
            return _commit_receipt(
                scope,
                chapter_id=target_chapter_id,
                previous_revision=previous_revision,
                committed_revision=desired_revision,
                content=content,
                noop=False,
                first_content=first_content,
            )

    async def _require_chapter(
        self,
        scope: WritingReadScope,
        *,
        chapter_id: str | None = None,
        created_ids: tuple[str, ...] | frozenset[str] = (),
    ) -> dict[str, Any]:
        target = str(chapter_id or "").strip() or scope.chapter_id
        if not target:
            raise WritingChapterMutationError(
                "writing_chapter_required",
                "Chapter operation requires a bound chapter",
            )
        # 章节绑定会话只能写绑定的章节或本 Run 由工具新建的章节；
        # 无章节绑定的全局对话不设此限制。
        if scope.chapter_id and target != scope.chapter_id and target not in created_ids:
            raise WritingChapterMutationError(
                "writing_chapter_scope_conflict",
                "Chapter-bound sessions may only edit the bound chapter or "
                "chapters created in this Run",
            )
        row = await self._db.fetch_one(
            "SELECT c.id, c.title, a.id AS article_id, a.content "
            "FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "LEFT JOIN articles AS a ON a.chapter_id = c.id "
            "WHERE c.id = ? AND o.book_id = ? AND o.type = 'writing'",
            [target, scope.book_id],
        )
        if row is None:
            raise WritingChapterMutationError(
                "writing_chapter_scope_conflict",
                "Chapter does not belong to the bound book",
            )
        if scope.session_id is not None:
            session = await self._db.fetch_one(
                "SELECT book_id, chapter_id FROM ai_sessions WHERE id = ?",
                [scope.session_id],
            )
            if session is None or str(session.get("book_id") or "") != scope.book_id:
                raise WritingChapterMutationError(
                    "writing_session_scope_conflict",
                    "Session does not belong to the bound book",
                )
            session_chapter = str(session.get("chapter_id") or "").strip()
            target_allowed = (
                target == scope.chapter_id
                or target in created_ids
                or not scope.chapter_id
            )
            if session_chapter and session_chapter != target and not target_allowed:
                raise WritingChapterMutationError(
                    "writing_chapter_scope_conflict",
                    "Chapter conflicts with the bound session",
                )
        return row

    async def _record_word_delta(
        self,
        book_id: str,
        *,
        previous: str,
        current: str,
    ) -> None:
        old_words = count_words(extract_text_from_lexical(previous)) if previous else 0
        new_words = count_words(extract_text_from_lexical(current)) if current else 0
        delta = new_words - old_words
        if delta == 0:
            return
        latest = await self._db.fetch_one(
            "SELECT total_words FROM book_word_stats WHERE book_id = ? "
            "ORDER BY date DESC LIMIT 1",
            [book_id],
        )
        if latest is None:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        await self._db.execute(
            "INSERT INTO book_word_stats (book_id, date, total_words) "
            "VALUES (?, ?, ?) ON CONFLICT(book_id, date) DO UPDATE SET "
            "total_words = excluded.total_words",
            [book_id, today, int(latest["total_words"]) + delta],
        )


def _validate_edit_arguments(
    *,
    content: str,
    base_revision: str,
    clear_content: bool,
) -> None:
    if not isinstance(content, str):
        raise WritingChapterMutationError(
            "writing_chapter_content_required",
            "Chapter content is required",
        )
    if len(content) > 250_000:
        raise WritingChapterMutationError(
            "writing_chapter_content_too_large",
            "Chapter content exceeds the supported limit",
        )
    if not str(base_revision or "").strip():
        raise WritingChapterMutationError(
            "writing_chapter_revision_required",
            "baseRevision is required",
        )
    if not content and not clear_content:
        raise WritingChapterMutationError(
            "writing_chapter_clear_requires_explicit_intent",
            "Empty content requires clearContent=true",
        )
    if clear_content and content:
        raise WritingChapterMutationError(
            "writing_chapter_clear_conflicts_with_content",
            "clearContent=true requires empty content",
        )


def _content_revision(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _commit_receipt(
    scope: WritingReadScope,
    *,
    chapter_id: str | None = None,
    previous_revision: str,
    committed_revision: str,
    content: str,
    noop: bool,
    first_content: bool = False,
) -> dict[str, Any]:
    receipt_chapter_id = str(chapter_id or scope.chapter_id)
    intent = hashlib.sha256(
        "\0".join((scope.book_id, receipt_chapter_id, previous_revision, content))
        .encode("utf-8")
    ).hexdigest()
    return {
        "schemaVersion": 1,
        "success": True,
        "bookId": scope.book_id,
        "chapterId": receipt_chapter_id,
        "previousRevision": previous_revision,
        "committedRevision": committed_revision,
        "intentDigest": f"sha256:{intent}",
        "contentCharacters": len(content),
        "noop": noop,
        "firstContent": first_content,
    }


def _bounded_text_length(value: int) -> int:
    if type(value) is not int or value < 1 or value > 48_000:
        raise WritingChapterMutationError(
            "writing_chapter_read_limit_invalid",
            "maxTextLength must be between 1 and 48000",
        )
    return value


__all__ = [
    "SqliteWritingChapterRepository",
    "WritingChapterMutationError",
]
