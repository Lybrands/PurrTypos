"""Read-only source access for a host-bound continuation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Callable

from application.continuation_context import ContinuationContextService
from domains.writing.tools.contracts import ToolResult, _err

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_read_continuation_source_section(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    del send_chunk
    book_id = str(ctx.get("bookId") or "").strip()
    section_id = str(args.get("sectionId") or "").strip()
    if not book_id:
        return _err({"error": "缺少宿主作品绑定或来源章节 ID"})
    try:
        service = ContinuationContextService(dependencies.db)
        payload = await service.read_source_section(book_id=book_id, section_id=section_id) if section_id else await service.list_source_sections(book_id=book_id, offset=int(args.get("offset") or 0), limit=int(args.get("limit") or 100))
    except Exception as error:
        return _err({"error": str(error)})
    return ToolResult(json.dumps({
        **payload,
        "scope": {
            "targetBookId": book_id,
            "readOnly": True,
            "mayWriteSource": False,
        },
    }, ensure_ascii=False))

