"""Persist Writing read results and supply them to subsequent host contexts."""

import hashlib
import json

from purra.cancellation import raise_if_stopped
from purra.json_values import thaw_json_mapping
from domains.read_materials import ReadMaterial
from domains.writing.execution_state import WritingExecutionStateFactory
from domains.writing.tools.contracts import ToolResult


_READS = frozenset({
    "getChapterContent", "batchGetChapterContents", "listWritingChapters",
    "getBookCharacters", "listBookCharacters", "getStoryBackground",
    "listSettingEntities", "getSettingEntities", "queryOutline", "getGlobalOutline", "listOutlines",
})
_SCOPE_FIELDS = ("bookId", "chapterId", "writingChapters", "availableOutlines",
                 "creationMode", "continuationBinding")


def _scope_key(scope):
    return json.dumps(thaw_json_mapping({key: scope.get(key) for key in _SCOPE_FIELDS}),
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WritingPreparedReads:
    def __init__(self, db):
        self._db = db

    def bind(self, name, handler):
        if name not in _READS:
            return handler

        async def read(scope, arguments, send_chunk):
            scope_key = _scope_key(scope)
            args = {key: value for key, value in arguments.items() if not key.startswith("__")}
            arguments_json = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256(f"{name}:{scope_key}:{arguments_json}".encode()).hexdigest()
            async with self._db.transaction():
                row = await self._db.fetch_one("SELECT content FROM writing_tool_cache WHERE cache_key = ?", [key])
                if row is not None:
                    return ToolResult(row["content"], from_cache=True)
                scope.pop("readToolCache", None)
                scope.pop("chapterContentCache", None)
                result = await handler(scope, arguments, send_chunk)
                try:
                    payload = json.loads(result.content)
                except ValueError:
                    payload = {}
                if not (isinstance(payload, dict) and payload.get("error")) and len(result.content.encode()) <= 262_144:
                    await self._db.execute(
                        "INSERT INTO writing_tool_cache (cache_key, scope_key, tool_name, arguments_json, content) "
                        "VALUES (?, ?, ?, ?, ?)", [key, scope_key, name, arguments_json, result.content],
                    )
                    await self._db.execute("DELETE FROM writing_tool_cache WHERE id IN "
                        "(SELECT id FROM writing_tool_cache ORDER BY id DESC LIMIT -1 OFFSET 128)")
                return result
        return read

    async def load(self, request, signal=None):
        if not request.tools_enabled:
            return ()
        scope = WritingExecutionStateFactory().create(request).domain
        if not scope.get("bookId"):
            return ()
        rows = await self._db.fetch_all(
            "SELECT cache_key, tool_name, arguments_json, content FROM writing_tool_cache "
            "WHERE scope_key = ? ORDER BY id DESC", [_scope_key(scope)],
        )
        materials = []
        for row in rows:
            raise_if_stopped(signal)
            if row["tool_name"] not in _READS:
                continue
            arguments = json.loads(row["arguments_json"])
            arguments.pop("bookId", None)
            materials.append(ReadMaterial(
                row["cache_key"], row["tool_name"], arguments, row["content"],
                {"bookId": scope["bookId"]},
            ))
        return tuple(materials)
