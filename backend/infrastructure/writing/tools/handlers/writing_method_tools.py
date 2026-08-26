"""Read-only, explicitly gated writing-method recommendation catalog."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from domains.writing.tools.contracts import ToolResult
from infrastructure.persistence.writing.sqlite_writing_method_repository import (
    SqliteWritingMethodRepository,
)

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_search_writing_methods(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    del ctx, send_chunk
    rows = await SqliteWritingMethodRepository(
        dependencies.db
    ).search_published_methods(
        str(args.get("query") or ""),
        limit=int(args.get("limit") or 8),
    )
    return ToolResult(json.dumps({
        "methods": [{
            "revisionId": row["id"],
            "methodId": row["method_id"],
            "versionNo": row["version_no"],
            "name": row["name"],
            "description": row.get("description") or "",
            "methodType": row["method_type"],
            "tags": row.get("tags") or [],
            "contentDigest": row["content_digest"],
        } for row in rows],
        "bindingChanged": False,
        "instruction": "只返回建议和理由；不得自行绑定、升级、解绑或调整优先级。",
    }, ensure_ascii=False))
