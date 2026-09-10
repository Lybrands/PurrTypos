"""Scope-isolated cache used only while a Writing read tool executes."""

from __future__ import annotations

import hashlib
import json

from purra.json_values import thaw_json_mapping
from domains.writing.tools.contracts import ToolResult


_READS = frozenset({
    "getChapterContent", "batchGetChapterContents", "listWritingChapters",
    "getBookCharacters", "listBookCharacters", "getStoryBackground",
    "listSettingEntities", "getSettingEntities", "queryOutline",
    "getGlobalOutline", "listOutlines",
})
_SCOPE_FIELDS = (
    "bookId", "chapterId", "writingChapters", "availableOutlines",
    "creationMode", "continuationBinding",
)
_MAX_ENTRIES = 128
_MAX_CONTENT_BYTES = 256 * 1024


def _scope_key(scope):
    return json.dumps(
        thaw_json_mapping({key: scope.get(key) for key in _SCOPE_FIELDS}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class WritingReadCache:
    """Wrap read handlers without making cached bodies model context."""

    def __init__(self, db):
        self._db = db

    def bind(self, name, handler):
        if name not in _READS:
            return handler

        async def read(scope, arguments, send_chunk):
            scope_key = _scope_key(scope)
            args = {
                key: value
                for key, value in arguments.items()
                if not key.startswith("__")
            }
            arguments_json = json.dumps(
                args,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            key = hashlib.sha256(
                f"continuation-history-v1:{name}:{scope_key}:{arguments_json}".encode()
            ).hexdigest()
            async with self._db.transaction():
                row = await self._db.fetch_one(
                    "SELECT content FROM writing_tool_cache WHERE cache_key = ?",
                    [key],
                )
                if row is not None:
                    return ToolResult(row["content"], from_cache=True)
                scope.pop("readToolCache", None)
                scope.pop("chapterContentCache", None)
                result = await handler(scope, arguments, send_chunk)
                try:
                    payload = json.loads(result.content)
                except ValueError:
                    payload = {}
                if (
                    isinstance(payload, dict)
                    and not payload.get("error")
                    and len(result.content.encode()) <= _MAX_CONTENT_BYTES
                ):
                    await self._db.execute(
                        "INSERT INTO writing_tool_cache "
                        "(cache_key, scope_key, tool_name, arguments_json, content) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [key, scope_key, name, arguments_json, result.content],
                    )
                    await self._db.execute(
                        "DELETE FROM writing_tool_cache WHERE id IN "
                        "(SELECT id FROM writing_tool_cache ORDER BY id DESC "
                        "LIMIT -1 OFFSET ?)",
                        [_MAX_ENTRIES],
                    )
                return result

        return read


__all__ = ["WritingReadCache"]
