"""Scope-isolated, bounded cache for successful screenplay reads."""

from __future__ import annotations

import hashlib
import json

from purra.json_values import thaw_json_mapping

from database.screenplay_tool_cache_schema import SCREENPLAY_READ_DEPENDENCIES


_MAX_ENTRIES = 128
_MAX_CONTENT_BYTES = 256 * 1024
_SCOPE_FIELDS = (
    "projectId", "sourceBookId", "sourceScope", "deliverableRevisionScope",
    "boundEpisodeNumber",
)


def screenplay_cache_identity(tool_name, scope, arguments):
    if tool_name not in SCREENPLAY_READ_DEPENDENCIES:
        raise ValueError(f"No read-cache dependency contract for {tool_name}")
    fields = _SCOPE_FIELDS
    if tool_name == "readScreenplayTaskDependencies":
        fields += ("taskId", "unitId", "dependencyPartKeys")
    bound_scope = thaw_json_mapping({
            key: scope[key] for key in fields if key in scope
        })
    key_content = json.dumps(
        [2, tool_name, bound_scope, thaw_json_mapping(arguments)],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    key = hashlib.sha256(key_content.encode("utf-8")).hexdigest()
    scope_key = json.dumps(bound_scope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return key, scope_key


async def cached_screenplay_read(db, tool_name, method, scope, arguments):
    key, scope_key = screenplay_cache_identity(tool_name, scope, arguments)
    arguments_json = json.dumps(thaw_json_mapping(arguments), ensure_ascii=False, sort_keys=True)
    async with db.transaction():
        row = await db.fetch_one(
            "SELECT content FROM screenplay_tool_cache WHERE cache_key = ?", [key],
        )
        if row is not None:
            await db.execute(
                "UPDATE screenplay_tool_cache SET scope_key = ?, arguments_json = ? WHERE cache_key = ?",
                [scope_key, arguments_json, key],
            )
            return json.loads(row["content"]), True
        result = await method(scope, arguments)
        content = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        if len(content.encode("utf-8")) <= _MAX_CONTENT_BYTES:
            await db.execute(
                "INSERT INTO screenplay_tool_cache (cache_key, tool_name, content, scope_key, arguments_json) "
                "VALUES (?, ?, ?, ?, ?)", [key, tool_name, content, scope_key, arguments_json],
            )
            await db.execute(
                "DELETE FROM screenplay_tool_cache WHERE id IN "
                "(SELECT id FROM screenplay_tool_cache ORDER BY id DESC LIMIT -1 OFFSET ?)",
                [_MAX_ENTRIES],
            )
        return result, False


__all__ = ["cached_screenplay_read"]
