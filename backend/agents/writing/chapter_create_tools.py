"""Chapter/volume creation tool for the replacement Writing Agent."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Mapping

from utils.id_utils import short_id8
from agents.writing.read_model import WritingReadScope, WritingReadScopeError
from agents.writing.read_tools import WRITING_READ_SCOPE_STATE_KEY
from application.domain_effect_bridge import get_broadcaster
from purra.cancellation import raise_if_stopped
from purra.contracts import (
    DomainEffect,
    ToolContextContract,
    ToolDataContract,
    ToolEffectState,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration

WRITING_CREATED_CHAPTER_IDS_STATE_KEY = "writingCreatedChapterIds"
MAX_CHAPTERS_PER_CALL = 20
MAX_TITLE_CHARS = 120


class WritingChapterCreateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


def created_chapter_ids(state) -> list[str]:
    """Chapters created by tools during this Run (authorization allow-list)."""

    raw = getattr(state, "domain", {}).get(WRITING_CREATED_CHAPTER_IDS_STATE_KEY)
    return [str(item) for item in raw] if isinstance(raw, (list, tuple)) else []


def _entries(arguments: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    # purra 会把参数冻结为 FrozenList/FrozenDict（Sequence/Mapping 子类，
    # 不是原生 list/tuple/dict），按结构化协议判断而不是具体容器类型。
    raw = arguments.get("chapters")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(raw)


class SqliteWritingChapterCreateRepository:
    def __init__(self, db) -> None:
        self._db = db

    async def default_parent_id(self, scope: WritingReadScope) -> str | None:
        """无显式 parentId 时，追加到绑定章节所在卷（无卷则挂书根）。"""

        if not scope.chapter_id:
            return None
        row = await self._db.fetch_one(
            "SELECT parent_id FROM outline_chapters AS c "
            "JOIN outlines AS o ON o.id = c.outline_id "
            "WHERE c.id = ? AND o.book_id = ? AND o.type = 'writing'",
            [scope.chapter_id, scope.book_id],
        )
        parent = str(row.get("parent_id") or "").strip() if row else ""
        return parent or None

    async def plan(
        self,
        scope: WritingReadScope,
        *,
        chapters: tuple[Mapping[str, Any], ...],
        default_parent_id: str | None,
    ) -> tuple[str, tuple[dict[str, Any], ...]]:
        """只读校验：返回 (写作大纲根 ID, 计划条目)；不产生任何写入。"""

        from application.continuation_identity import require_editable_identity

        if not chapters or len(chapters) > MAX_CHAPTERS_PER_CALL:
            raise WritingChapterCreateError(
                "writing_chapter_batch_invalid",
                f"chapters must contain 1 to {MAX_CHAPTERS_PER_CALL} entries",
            )
        outline_row = await self._db.fetch_one(
            "SELECT id FROM outlines WHERE book_id = ? AND type = 'writing'",
            [scope.book_id],
        )
        if outline_row is None:
            raise WritingChapterCreateError(
                "writing_outline_missing",
                "The bound book has no writing outline to append chapters to",
            )
        writing_outline_id = str(outline_row["id"])

        planned: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for entry in chapters:
            title = str(entry.get("title") or "").strip()
            if not title or len(title) > MAX_TITLE_CHARS:
                raise WritingChapterCreateError(
                    "writing_chapter_title_invalid",
                    f"Each chapter title must be 1 to {MAX_TITLE_CHARS} characters",
                )
            if title in seen_titles:
                raise WritingChapterCreateError(
                    "writing_chapter_title_duplicate",
                    "Chapter titles inside one batch must be unique",
                )
            seen_titles.add(title)
            parent_id = str(entry.get("parentId") or "").strip() or default_parent_id
            is_volume = bool(entry.get("isVolume"))
            if parent_id:
                parent = await self._db.fetch_one(
                    "SELECT c.id FROM outline_chapters AS c "
                    "JOIN outlines AS o ON o.id = c.outline_id "
                    "JOIN outlines AS vo ON vo.writing_chapter_id = c.id "
                    "AND vo.type = 'volume' "
                    "WHERE c.id = ? AND o.book_id = ? AND o.type = 'writing'",
                    [parent_id, scope.book_id],
                )
                if parent is None:
                    raise WritingChapterCreateError(
                        "writing_chapter_parent_invalid",
                        "parentId must reference a volume in the bound book",
                    )
                await require_editable_identity(self._db, parent_id)
            planned.append({
                "title": title,
                "parentId": parent_id,
                "isVolume": is_volume,
            })
        return writing_outline_id, tuple(planned)

    async def create(
        self,
        scope: WritingReadScope,
        *,
        chapters: tuple[Mapping[str, Any], ...],
        default_parent_id: str | None,
        signal=None,
    ) -> dict[str, Any]:
        writing_outline_id, planned = await self.plan(
            scope,
            chapters=chapters,
            default_parent_id=default_parent_id,
        )
        raise_if_stopped(signal)
        created: list[dict[str, Any]] = []
        async with self._db.transaction(
            cancellation_linearizable=(
                not self._db.current_task_owns_transaction()
            ),
        ):
            next_order = await self._next_sort(writing_outline_id)
            for entry in planned:
                raise_if_stopped(signal)
                chapter_id = short_id8()
                await self._db.execute(
                    "INSERT INTO outline_chapters "
                    "(id, outline_id, title, level, sort, parent_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        chapter_id,
                        writing_outline_id,
                        entry["title"],
                        1,
                        next_order,
                        entry["parentId"],
                    ],
                )
                await self._upsert_chapter_outline(
                    writing_outline_id,
                    chapter_id,
                    entry["title"],
                    entry["parentId"],
                    entry["isVolume"],
                )
                created.append({
                    "chapterId": chapter_id,
                    "title": entry["title"],
                    "order": next_order,
                    "isVolume": entry["isVolume"],
                })
                next_order += 1

        return {
            "schemaVersion": 1,
            "bookId": scope.book_id,
            "chapters": created,
        }

    async def _next_sort(self, writing_outline_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sort), -1) AS max_sort "
            "FROM outline_chapters WHERE outline_id = ?",
            [writing_outline_id],
        )
        return int((row or {}).get("max_sort") or 0) + 1

    async def _upsert_chapter_outline(
        self,
        writing_outline_id: str,
        chapter_id: str,
        title: str,
        parent_chapter_id: str | None,
        is_volume: bool,
    ) -> None:
        """Mirror the chapters router: keep outlines in sync with the tree."""

        existing = await self._db.fetch_one(
            "SELECT id FROM outlines WHERE writing_chapter_id = ?", [chapter_id],
        )
        if existing:
            await self._db.execute(
                "UPDATE outlines SET title = ? WHERE writing_chapter_id = ?",
                [title, chapter_id],
            )
            return
        parent_outline = await self._db.fetch_one(
            "SELECT book_id FROM outlines WHERE id = ?", [writing_outline_id],
        )
        book_id = parent_outline["book_id"] if parent_outline else None
        parent_outline_id = None
        if parent_chapter_id:
            vol_outline = await self._db.fetch_one(
                "SELECT id FROM outlines WHERE writing_chapter_id = ?",
                [parent_chapter_id],
            )
            parent_outline_id = str(vol_outline["id"]) if vol_outline else None
        outline_type = "volume" if is_volume else "chapter"
        max_sort = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sort), 0) AS m FROM outlines "
            "WHERE type = ? AND book_id IS ?",
            [outline_type, book_id],
        )
        sort = (int(max_sort["m"]) if max_sort else 0) + 1
        await self._db.execute(
            "INSERT INTO outlines "
            "(id, title, type, sort, book_id, writing_chapter_id, "
            "parent_outline_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                short_id8(),
                title,
                outline_type,
                sort,
                book_id,
                chapter_id,
                parent_outline_id,
            ],
        )


def build_writing_chapter_create_registrations(db) -> tuple[ToolRegistration, ...]:
    repository = SqliteWritingChapterCreateRepository(db)

    def _scope(state) -> WritingReadScope:
        return WritingReadScope.from_mapping(
            getattr(state, "domain", {}).get(WRITING_READ_SCOPE_STATE_KEY)
        )

    async def validate_create(state, arguments, signal=None):
        del signal
        try:
            scope = _scope(state)
            await repository.plan(
                scope,
                chapters=_entries(arguments),
                default_parent_id=await repository.default_parent_id(scope),
            )
        except (WritingChapterCreateError, WritingReadScopeError) as error:
            return getattr(error, "code", "writing_chapter_create_failed")
        return None

    async def create_chapters(state, arguments, signal=None):
        try:
            scope = _scope(state)
            payload = await repository.create(
                scope,
                chapters=_entries(arguments),
                default_parent_id=await repository.default_parent_id(scope),
                signal=signal,
            )
            domain = getattr(state, "domain", None)
            if domain is not None:
                domain[WRITING_CREATED_CHAPTER_IDS_STATE_KEY] = [
                    *created_chapter_ids(state),
                    *(item["chapterId"] for item in payload["chapters"]),
                ]
            # 建章落库的瞬间直接发布，章节列表据此实时刷新。
            get_broadcaster().publish(
                getattr(state, "run_id", None),
                "writing.chapters_created",
                {"bookId": payload["bookId"], "chapters": payload["chapters"]},
            )
            return ToolHandlerResult(
                json.dumps(payload, ensure_ascii=False),
                effects=(DomainEffect(
                    type="writing.chapters_created",
                    payload={
                        "bookId": payload["bookId"],
                        "chapters": payload["chapters"],
                    },
                ),),
                effect_state=ToolEffectState.COMMITTED,
            )
        except (WritingChapterCreateError, WritingReadScopeError) as error:
            return ToolHandlerResult(
                json.dumps(
                    {"success": False, "code": error.code, "error": str(error)},
                    ensure_ascii=False,
                ),
                error_code=error.code,
                effect_state=ToolEffectState.NOT_STARTED,
            )

    registration = ToolRegistration(
        schema=ToolSchema(
            name="createWritingChapters",
            description=(
                "在当前书写作目录末尾批量创建章节或卷（单次最多 "
                f"{MAX_CHAPTERS_PER_CALL} 条）。默认追加到当前绑定章节所在卷"
                "（无卷则挂书根）；parentId 必须是本书中的卷。"
                "创建后可用 readWritingChapters 读取新章，用 "
                "editChapterContent（传 chapterId）写入正文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "chapters": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_CHAPTERS_PER_CALL,
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_TITLE_CHARS,
                                },
                                "isVolume": {"type": "boolean"},
                                "parentId": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 512,
                                },
                            },
                            "required": ["title"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["chapters"],
                "additionalProperties": False,
            },
            display_names={"zh-CN": "创建章节/卷", "en": "Create Chapters"},
        ),
        handler=create_chapters,
        policy=ToolPolicy(
            ToolExecutionMode.CONFIRM,
            "创建章节/卷",
            ToolRiskLevel.WRITE,
        ),
        scope_validator=validate_create,
        cancellation_linearizable=True,
        context_contract=ToolContextContract(
            prerequisite_tools=("listWritingChapters",),
            mandatory_context_keys=("binding.bookId",),
            produces=("writing.chaptersCreated",),
        ),
        data_contract=ToolDataContract(
            model_owned_paths=("chapters",),
            # chapterId 是 editChapterContent 的 model-owned 参数（可选目标章），
            # 这里不能再声明为 host-bound，否则 catalog 数据路径所有权冲突。
            host_bound_paths=("bookId", "sessionId"),
        ),
        operation_display_params=lambda state, arguments, call: {
            "displayNames": {"zh-CN": "创建章节/卷", "en": "Create Chapters"},
            "toolArguments": {
                "chapters": _display_chapters(arguments.get("chapters")),
            },
        },
    )
    return (registration,)


def _display_chapters(raw) -> list[dict[str, Any]]:
    entries = (
        list(raw)
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes))
        else []
    )
    projected = []
    for entry in list(entries)[:8]:
        if not isinstance(entry, Mapping):
            continue
        title = str(entry.get("title") or "").strip()[:120]
        if not title:
            continue
        projected.append({
            "title": title,
            **({"isVolume": True} if entry.get("isVolume") else {}),
        })
    return projected
