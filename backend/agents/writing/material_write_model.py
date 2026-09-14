"""CAS-protected material updates for the replacement Writing Agent."""

from __future__ import annotations

from typing import Any

from agents.writing.read_model import (
    SqliteWritingReadRepository,
    WritingReadScope,
    WritingReadScopeError,
)
from agents.writing.revisions import intent_digest, record_revision, text_revision
from purra.cancellation import raise_if_stopped


class WritingMaterialMutationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class SqliteWritingMaterialRepository:
    def __init__(self, db) -> None:
        self._db = db
        self._reads = SqliteWritingReadRepository(db)

    async def validate_story_background(
        self, scope: WritingReadScope, *, content: str,
        base_revision: str, clear_content: bool,
    ) -> None:
        _validate_text_update("background", content, base_revision, clear_content)
        await self._validate_scope(scope)
        row = await self._db.fetch_one(
            "SELECT content FROM story_background WHERE book_id = ?", [scope.book_id]
        )
        _require_revision(
            text_revision((row or {}).get("content")), base_revision,
            text_revision(content), "writing_story_background_revision_conflict",
        )

    async def commit_story_background(
        self, scope: WritingReadScope, *, content: str,
        base_revision: str, clear_content: bool, signal=None,
    ) -> dict[str, Any]:
        _validate_text_update("background", content, base_revision, clear_content)
        raise_if_stopped(signal)
        async with self._write_transaction():
            await self._validate_scope(scope)
            row = await self._db.fetch_one(
                "SELECT content FROM story_background WHERE book_id = ?", [scope.book_id]
            )
            previous = str((row or {}).get("content") or "")
            receipt = _text_receipt(
                "story_background", scope.book_id, previous, content, base_revision
            )
            if receipt["noop"]:
                return receipt
            _require_revision(
                text_revision(previous), base_revision, text_revision(content),
                "writing_story_background_revision_conflict",
            )
            raise_if_stopped(signal)
            await self._db.execute(
                "INSERT INTO story_background_history "
                "(book_id, before_content, after_content, source) VALUES (?, ?, ?, 'ai')",
                [scope.book_id, previous, content],
            )
            await self._db.execute(
                "INSERT INTO story_background (book_id, content, update_time) "
                "VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(book_id) DO UPDATE SET "
                "content = excluded.content, update_time = CURRENT_TIMESTAMP",
                [scope.book_id, content],
            )
            return receipt

    async def validate_global_outline(
        self, scope: WritingReadScope, *, markdown: str,
        base_revision: str, clear_content: bool,
    ) -> None:
        _validate_text_update("outline", markdown, base_revision, clear_content)
        row = await self._require_global_outline(scope)
        _require_revision(
            text_revision(row.get("markdown_content")), base_revision,
            text_revision(markdown), "writing_global_outline_revision_conflict",
        )

    async def commit_global_outline(
        self, scope: WritingReadScope, *, markdown: str,
        base_revision: str, clear_content: bool, signal=None,
    ) -> dict[str, Any]:
        _validate_text_update("outline", markdown, base_revision, clear_content)
        raise_if_stopped(signal)
        async with self._write_transaction():
            row = await self._require_global_outline(scope)
            previous = str(row.get("markdown_content") or "")
            receipt = _text_receipt(
                "global_outline", str(row["id"]), previous, markdown, base_revision,
                book_id=scope.book_id,
            )
            if receipt["noop"]:
                return receipt
            _require_revision(
                text_revision(previous), base_revision, text_revision(markdown),
                "writing_global_outline_revision_conflict",
            )
            raise_if_stopped(signal)
            await self._db.execute(
                "INSERT INTO outline_history "
                "(outline_id, before_title, before_type, before_markdown_content, "
                "before_xmind_data, source) VALUES (?, ?, ?, ?, ?, 'ai')",
                [row["id"], row.get("title"), row.get("type"), previous, row.get("xmind_data")],
            )
            await self._db.execute(
                "UPDATE outlines SET markdown_content = ? WHERE id = ?",
                [markdown, row["id"]],
            )
            return receipt

    async def validate_character(
        self, scope: WritingReadScope, *, character_id: int,
        base_revision: str, patch: dict[str, str],
    ) -> None:
        _validate_record_patch("character", character_id, base_revision, patch)
        row = await self._require_character(scope, character_id)
        desired = _apply_patch(_character_values(row), patch)
        _require_revision(
            record_revision("character", _character_values(row)), base_revision,
            record_revision("character", desired),
            "writing_character_revision_conflict",
        )

    async def commit_character(
        self, scope: WritingReadScope, *, character_id: int,
        base_revision: str, patch: dict[str, str], signal=None,
    ) -> dict[str, Any]:
        _validate_record_patch("character", character_id, base_revision, patch)
        raise_if_stopped(signal)
        async with self._write_transaction():
            row = await self._require_character(scope, character_id)
            previous = _character_values(row)
            desired = _apply_patch(previous, patch)
            receipt = _record_receipt(
                "character", scope.book_id, character_id, previous, desired, base_revision
            )
            if receipt["noop"]:
                return receipt
            _require_revision(
                record_revision("character", previous), base_revision,
                record_revision("character", desired),
                "writing_character_revision_conflict",
            )
            raise_if_stopped(signal)
            await self._db.execute(
                "INSERT INTO character_history "
                "(character_id, before_name, before_tags, before_profile_md, "
                "after_name, after_tags, after_profile_md, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'ai')",
                [character_id, previous["name"], previous["tags"], previous["profileMd"],
                 desired["name"], desired["tags"], desired["profileMd"]],
            )
            await self._db.execute(
                "UPDATE characters SET name = ?, tags = ?, profile_md = ? WHERE id = ?",
                [desired["name"], desired["tags"], desired["profileMd"], character_id],
            )
            return receipt

    async def validate_setting_entity(
        self, scope: WritingReadScope, *, entity_id: int,
        base_revision: str, patch: dict[str, str],
    ) -> None:
        _validate_record_patch("setting entity", entity_id, base_revision, patch)
        row = await self._require_setting_entity(scope, entity_id)
        desired = _apply_patch(_entity_values(row), patch)
        _require_revision(
            record_revision("setting_entity", _entity_values(row)), base_revision,
            record_revision("setting_entity", desired),
            "writing_setting_entity_revision_conflict",
        )

    async def commit_setting_entity(
        self, scope: WritingReadScope, *, entity_id: int,
        base_revision: str, patch: dict[str, str], signal=None,
    ) -> dict[str, Any]:
        _validate_record_patch("setting entity", entity_id, base_revision, patch)
        raise_if_stopped(signal)
        async with self._write_transaction():
            row = await self._require_setting_entity(scope, entity_id)
            previous = _entity_values(row)
            desired = _apply_patch(previous, patch)
            receipt = _record_receipt(
                "setting_entity", scope.book_id, entity_id, previous, desired, base_revision
            )
            if receipt["noop"]:
                return receipt
            _require_revision(
                record_revision("setting_entity", previous), base_revision,
                record_revision("setting_entity", desired),
                "writing_setting_entity_revision_conflict",
            )
            raise_if_stopped(signal)
            await self._db.execute(
                "INSERT INTO setting_entity_history "
                "(entity_id, before_name, before_tags, before_profile_md, "
                "after_name, after_tags, after_profile_md, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'ai')",
                [entity_id, previous["name"], previous["tags"], previous["profileMd"],
                 desired["name"], desired["tags"], desired["profileMd"]],
            )
            await self._db.execute(
                "UPDATE setting_entities SET name = ?, tags = ?, profile_md = ? WHERE id = ?",
                [desired["name"], desired["tags"], desired["profileMd"], entity_id],
            )
            return receipt

    async def _validate_scope(self, scope: WritingReadScope) -> None:
        try:
            await self._reads.validate_scope(scope)
        except WritingReadScopeError as error:
            raise WritingMaterialMutationError(error.code, str(error)) from error

    def _write_transaction(self):
        return self._db.transaction(
            cancellation_linearizable=not self._db.current_task_owns_transaction()
        )

    async def _require_global_outline(self, scope: WritingReadScope) -> dict[str, Any]:
        await self._validate_scope(scope)
        rows = await self._db.fetch_all(
            "SELECT id, title, type, markdown_content, xmind_data FROM outlines "
            "WHERE book_id = ? AND type = 'global' ORDER BY create_time, id LIMIT 2",
            [scope.book_id],
        )
        if not rows:
            raise WritingMaterialMutationError(
                "writing_global_outline_not_found", "The bound book has no global outline"
            )
        if len(rows) != 1:
            raise WritingMaterialMutationError(
                "writing_global_outline_ambiguous", "The bound book has multiple global outlines"
            )
        return rows[0]

    async def _require_character(
        self, scope: WritingReadScope, character_id: int,
    ) -> dict[str, Any]:
        await self._validate_scope(scope)
        row = await self._db.fetch_one(
            "SELECT id, name, tags, profile_md FROM characters WHERE id = ? AND book_id = ?",
            [character_id, scope.book_id],
        )
        if row is None:
            raise WritingMaterialMutationError(
                "writing_character_scope_conflict", "Character does not belong to the bound book"
            )
        return row

    async def _require_setting_entity(
        self, scope: WritingReadScope, entity_id: int,
    ) -> dict[str, Any]:
        await self._validate_scope(scope)
        row = await self._db.fetch_one(
            "SELECT id, name, tags, profile_md FROM setting_entities "
            "WHERE id = ? AND book_id = ?",
            [entity_id, scope.book_id],
        )
        if row is None:
            raise WritingMaterialMutationError(
                "writing_setting_entity_scope_conflict",
                "Setting entity does not belong to the bound book",
            )
        return row


def _validate_text_update(
    kind: str, content: object, base_revision: object, clear_content: object,
) -> None:
    if not isinstance(content, str):
        raise WritingMaterialMutationError(f"writing_{kind}_content_required", "Content is required")
    if len(content) > 250_000:
        raise WritingMaterialMutationError(f"writing_{kind}_content_too_large", "Content is too large")
    _require_base_revision(base_revision)
    if not content and clear_content is not True:
        raise WritingMaterialMutationError(
            f"writing_{kind}_clear_requires_explicit_intent",
            "Empty content requires clearContent=true",
        )
    if content and clear_content is True:
        raise WritingMaterialMutationError(
            f"writing_{kind}_clear_conflicts_with_content",
            "clearContent=true requires empty content",
        )


def _validate_record_patch(
    kind: str, record_id: object, base_revision: object, patch: dict[str, str],
) -> None:
    if type(record_id) is not int or record_id < 1:
        raise WritingMaterialMutationError("tool_input_invalid", f"{kind} id is invalid")
    _require_base_revision(base_revision)
    if not patch:
        raise WritingMaterialMutationError("tool_input_invalid", "At least one field must be changed")
    if any(not isinstance(value, str) for value in patch.values()):
        raise WritingMaterialMutationError("tool_input_invalid", "Patch fields must be strings")
    if "name" in patch and not patch["name"].strip():
        raise WritingMaterialMutationError("tool_input_invalid", "name cannot be empty")
    if any(len(value) > 250_000 for value in patch.values()):
        raise WritingMaterialMutationError("tool_input_invalid", "Patch field is too large")


def _require_base_revision(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise WritingMaterialMutationError(
            "writing_material_revision_required", "baseRevision is required"
        )


def _require_revision(actual: str, base: str, desired: str, code: str) -> None:
    if actual not in {base, desired}:
        raise WritingMaterialMutationError(code, "Material changed after it was read")


def _character_values(row: dict[str, Any]) -> dict[str, str]:
    return {"name": str(row.get("name") or ""), "tags": str(row.get("tags") or ""),
            "profileMd": str(row.get("profile_md") or "")}


def _entity_values(row: dict[str, Any]) -> dict[str, str]:
    return _character_values(row)


def _apply_patch(previous: dict[str, str], patch: dict[str, str]) -> dict[str, str]:
    return {**previous, **patch}


def _text_receipt(
    kind: str, target_id: str, previous: str, desired: str, base_revision: str,
    *, book_id: str | None = None,
) -> dict[str, Any]:
    committed = text_revision(desired)
    return {
        "schemaVersion": 1, "success": True, "bookId": book_id or target_id,
        "targetId": target_id, "previousRevision": text_revision(previous),
        "committedRevision": committed,
        "intentDigest": intent_digest(kind, {
            "targetId": target_id, "baseRevision": base_revision, "content": desired,
        }),
        "contentCharacters": len(desired), "noop": text_revision(previous) == committed,
    }


def _record_receipt(
    kind: str, book_id: str, target_id: int, previous: dict[str, str],
    desired: dict[str, str], base_revision: str,
) -> dict[str, Any]:
    committed = record_revision(kind, desired)
    return {
        "schemaVersion": 1, "success": True, "bookId": book_id,
        "targetId": target_id, "previousRevision": record_revision(kind, previous),
        "committedRevision": committed,
        "intentDigest": intent_digest(kind, {
            "targetId": target_id, "baseRevision": base_revision, "values": desired,
        }),
        "noop": record_revision(kind, previous) == committed,
    }


__all__ = ["SqliteWritingMaterialRepository", "WritingMaterialMutationError"]
