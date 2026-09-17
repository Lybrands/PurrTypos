"""CAS-protected material writes for the replacement Writing Agent."""

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

    async def validate_create_setting_entity(
        self, scope: WritingReadScope, *, name: str, entity_type: str,
        tags: str = "", profile_md: str = "",
    ) -> None:
        values = _validate_entity_create(name, entity_type, tags, profile_md)
        await self._validate_scope(scope)
        await self._require_entity_name_available(scope, values["name"])

    async def commit_create_setting_entity(
        self, scope: WritingReadScope, *, name: str, entity_type: str,
        tags: str = "", profile_md: str = "", signal=None,
    ) -> dict[str, Any]:
        values = _validate_entity_create(name, entity_type, tags, profile_md)
        raise_if_stopped(signal)
        async with self._write_transaction():
            await self._validate_scope(scope)
            await self._require_entity_name_available(scope, values["name"])
            raise_if_stopped(signal)
            entity_id = int(await self._db.execute_and_get_id(
                "INSERT INTO setting_entities "
                "(book_id, entity_type, name, tags, profile_md) VALUES (?, ?, ?, ?, ?)",
                [scope.book_id, values["entityType"], values["name"],
                 values["tags"], values["profileMd"]],
            ))
            return {
                "schemaVersion": 1, "success": True, "bookId": scope.book_id,
                "targetId": entity_id, "name": values["name"],
                "entityType": values["entityType"],
                # 与读取侧 record_revision 同组成（不含 entityType），
                # 模型可直接把该回执当作后续 updateSettingEntity 的 baseRevision。
                "committedRevision": record_revision("setting_entity", {
                    "name": values["name"], "tags": values["tags"],
                    "profileMd": values["profileMd"],
                }),
                "intentDigest": intent_digest("setting_entity_create", {
                    "bookId": scope.book_id, **values,
                }),
                "noop": False,
            }

    async def validate_delete_setting_entity(
        self, scope: WritingReadScope, *, entity_id: int, base_revision: str,
    ) -> None:
        _validate_entity_id("setting entity", entity_id)
        _require_base_revision(base_revision)
        row = await self._require_deletable_entity(scope, entity_id)
        _require_current_revision(row, base_revision)

    async def commit_delete_setting_entity(
        self, scope: WritingReadScope, *, entity_id: int,
        base_revision: str, signal=None,
    ) -> dict[str, Any]:
        _validate_entity_id("setting entity", entity_id)
        _require_base_revision(base_revision)
        raise_if_stopped(signal)
        async with self._write_transaction():
            row = await self._require_deletable_entity(scope, entity_id)
            previous = _entity_values(row)
            _require_current_revision(row, base_revision)
            raise_if_stopped(signal)
            # 与宿主删除语义一致：连同修订历史一起清理，不留孤儿历史行。
            await self._db.execute(
                "DELETE FROM setting_entity_history WHERE entity_id = ?",
                [entity_id],
            )
            await self._db.execute(
                "DELETE FROM setting_entities WHERE id = ?", [entity_id],
            )
            return {
                "schemaVersion": 1, "success": True, "bookId": scope.book_id,
                "targetId": entity_id, "name": previous["name"],
                "previousRevision": record_revision("setting_entity", previous),
                "committedRevision": None,
                "intentDigest": intent_digest("setting_entity_delete", {
                    "bookId": scope.book_id, "targetId": entity_id,
                    "baseRevision": base_revision,
                }),
                "noop": False,
            }

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

    async def _require_entity_name_available(
        self, scope: WritingReadScope, name: str,
    ) -> None:
        row = await self._db.fetch_one(
            "SELECT id FROM setting_entities WHERE book_id = ? AND name = ?",
            [scope.book_id, name],
        )
        if row is not None:
            raise WritingMaterialMutationError(
                "writing_setting_entity_duplicate_name",
                "A setting entity with the same name already exists in the bound book",
            )

    async def _require_deletable_entity(
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
                "writing_setting_entity_not_found",
                "Setting entity does not exist in the bound book",
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
    _validate_entity_id(kind, record_id)
    _require_base_revision(base_revision)
    if not patch:
        raise WritingMaterialMutationError("tool_input_invalid", "At least one field must be changed")
    if any(not isinstance(value, str) for value in patch.values()):
        raise WritingMaterialMutationError("tool_input_invalid", "Patch fields must be strings")
    if "name" in patch and not patch["name"].strip():
        raise WritingMaterialMutationError("tool_input_invalid", "name cannot be empty")
    if any(len(value) > 250_000 for value in patch.values()):
        raise WritingMaterialMutationError("tool_input_invalid", "Patch field is too large")


SETTING_ENTITY_TYPES = ("location", "faction", "item", "other")


def _validate_entity_create(
    name: object, entity_type: object, tags: object, profile_md: object,
) -> dict[str, str]:
    normalized_name = str(name or "").strip() if isinstance(name, str) else ""
    if not normalized_name or len(normalized_name) > 200:
        raise WritingMaterialMutationError(
            "tool_input_invalid", "name must be 1 to 200 characters"
        )
    normalized_type = str(entity_type or "").strip().lower()
    if normalized_type not in SETTING_ENTITY_TYPES:
        raise WritingMaterialMutationError(
            "tool_input_invalid",
            "entityType must be one of location, faction, item, other",
        )
    values = {"name": normalized_name, "entityType": normalized_type}
    for key, raw, limit in (("tags", tags, 10_000), ("profileMd", profile_md, 250_000)):
        if not isinstance(raw, str):
            raise WritingMaterialMutationError(
                "tool_input_invalid", f"{key} must be a string"
            )
        if len(raw) > limit:
            raise WritingMaterialMutationError(
                "tool_input_invalid", f"{key} is too large"
            )
        values[key] = raw
    return values


def _validate_entity_id(kind: str, record_id: object) -> None:
    if type(record_id) is not int or record_id < 1:
        raise WritingMaterialMutationError("tool_input_invalid", f"{kind} id is invalid")


def _require_current_revision(row: dict[str, Any], base_revision: str) -> None:
    if record_revision("setting_entity", _entity_values(row)) != base_revision:
        raise WritingMaterialMutationError(
            "writing_setting_entity_revision_conflict",
            "Setting entity changed after it was read",
        )


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
        "targetId": target_id, "name": desired["name"],
        "previousRevision": record_revision(kind, previous),
        "committedRevision": committed,
        "intentDigest": intent_digest(kind, {
            "targetId": target_id, "baseRevision": base_revision, "values": desired,
        }),
        "noop": record_revision(kind, previous) == committed,
    }


__all__ = ["SqliteWritingMaterialRepository", "WritingMaterialMutationError"]
