"""Memory, spark idea, and foreshadowing tool handlers."""

from __future__ import annotations

import json
from typing import Any, Callable

from constants import FORESHADOWING_TYPES, SPARK_IDEA_LAYERS, SPARK_IDEA_LAYER_ORDERED
from services.tool_runtime import (
    ToolResult,
    _err,
    _runtime_chapter_id,
    resolve_book_id_for_tools,
    tool,
)


@tool("addSparkIdea")
async def _tool_add_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    mem_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
    cid = args.get("chapterId") or _runtime_chapter_id(ctx) or None
    character_id = args.get("characterId")
    layer_num = args.get("layer") if isinstance(args.get("layer"), (int, float)) else None
    layer_valid = layer_num is not None and int(layer_num) in (0, 1, 2, 3)

    if not bid:
        # ai_memories.book_id NOT NULL，缺 bookId 必然 INSERT IntegrityError；前置拦下给 LLM 一个能看懂的错
        return _err({"success": False, "error": "缺少有效 bookId，无法写入本书设定"})
    if not layer_valid or not mem_content:
        msg = "设定内容不能为空" if not mem_content else f"layer 须为 0/1/2/3（{' / '.join(SPARK_IDEA_LAYER_ORDERED)}）"
        return _err({"success": False, "error": msg})

    layer_str = SPARK_IDEA_LAYERS[int(layer_num)]
    try:
        from services import memory_service
        row = await memory_service.add_spark_idea(bid, layer_str, mem_content, cid, character_id)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已添加【{layer_str}设定】",
            "id": row.get("id") if row else None,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("updateSparkIdea")
async def _tool_update_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """修改一条已有设定（content / layer / chapterId / characterId），id 必填。

    关联实体语义：显式传 None 视为清空；不传（key 不在 args 里）视为不变。
    """
    bid = resolve_book_id_for_tools(ctx, args)
    raw_id = args.get("id")
    sid = str(raw_id).strip() if raw_id is not None else ""
    new_content_raw = args.get("content")
    new_content = str(new_content_raw).strip() if isinstance(new_content_raw, str) else None
    new_layer_raw = args.get("layer")
    new_layer_num = int(new_layer_raw) if isinstance(new_layer_raw, (int, float)) else None
    new_layer_valid = new_layer_num is None or new_layer_num in (0, 1, 2, 3)
    has_chapter = "chapterId" in args
    has_character = "characterId" in args
    new_chapter_id = args.get("chapterId")
    new_character_id = args.get("characterId")

    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not sid:
        return _err({"success": False, "error": "缺少 id：updateSparkIdea 必须指定要更新的条目"})
    if not new_layer_valid:
        return _err({"success": False, "error": f"layer 须为 0/1/2/3（{' / '.join(SPARK_IDEA_LAYER_ORDERED)}）"})
    if (
        (new_content is None or new_content == "")
        and new_layer_num is None
        and not has_chapter
        and not has_character
    ):
        return _err({"success": False, "error": "noop：content / layer / chapterId / characterId 至少需要传其中一个", "noop": True})

    try:
        from services import memory_service
        existing = await memory_service.get_spark_ideas_by_ids([sid])
        if not existing or str(existing[0].get("book_id") or "") != str(bid):
            return _err({
                "success": False,
                "error": "该设定条目不属于当前书籍，已拒绝更新",
            })
        update_payload: dict[str, Any] = {}
        if new_content is not None and new_content != "":
            update_payload["content"] = new_content
        if new_layer_num is not None:
            update_payload["layer"] = SPARK_IDEA_LAYERS[new_layer_num]
        if has_chapter:
            update_payload["chapter_id"] = str(new_chapter_id) if new_chapter_id is not None else None
        if has_character:
            update_payload["character_id"] = int(new_character_id) if new_character_id is not None else None

        updated = await memory_service.update_spark_idea(sid, update_payload)
        if not updated:
            return _err({"success": False, "error": f"未找到 id={sid} 的设定条目"})
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已更新【{updated.get('layer')}设定】",
            "id": updated.get("id"),
            "layer": updated.get("layer"),
            "content": updated.get("content"),
            "chapter_id": updated.get("chapter_id"),
            "character_id": updated.get("character_id"),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("deleteSparkIdea")
async def _tool_delete_spark_idea(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """物理删除一条设定；删除前先取出 content/layer 用于回显，便于 LLM 在回复中复述。"""
    bid = resolve_book_id_for_tools(ctx, args)
    raw_id = args.get("id")
    sid = str(raw_id).strip() if raw_id is not None else ""
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not sid:
        return _err({"success": False, "error": "缺少 id：deleteSparkIdea 必须指定要删除的条目"})

    try:
        from services import memory_service
        existed = await memory_service.get_spark_ideas_by_ids([sid])
        if not existed or str(existed[0].get("book_id") or "") != str(bid):
            return _err({
                "success": False,
                "error": "该设定条目不属于当前书籍，已拒绝删除",
            })
        target = existed[0]
        await memory_service.delete_spark_idea(sid)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已删除【{target.get('layer')}设定】",
            "id": target.get("id"),
            "layer": target.get("layer"),
            "content": target.get("content"),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("addForeshadowing")
async def _tool_add_foreshadowing(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    for_chapter_id = args.get("chapterId") or _runtime_chapter_id(ctx) or None
    for_content = str(args.get("content") or "").strip() if isinstance(args.get("content"), str) else ""
    for_type = args.get("type") or FORESHADOWING_TYPES[0]

    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法写入伏笔"})
    if not for_chapter_id or not for_content:
        msg = "缺少 chapterId（埋入章节）" if not for_chapter_id else "伏笔内容不能为空"
        return _err({"success": False, "error": msg})
    if for_type not in FORESHADOWING_TYPES:
        return _err({"success": False, "error": f"type 须为：{' / '.join(FORESHADOWING_TYPES)}"})

    try:
        from services import memory_service
        row = await memory_service.add_foreshadowing(bid, for_chapter_id, for_content, for_type, None)
        return ToolResult(json.dumps({
            "success": True,
            "message": f"已添加伏笔【{for_type}】",
            "id": row.get("id") if row else None,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("searchSparkIdeas")
async def _tool_search_spark_ideas(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法检索本书设定"})

    query = args.get("query") or ""
    layer = args.get("layer")
    cid = args.get("chapterId") or _runtime_chapter_id(ctx) or None
    limit = args.get("limit") or 15

    try:
        from services import memory_service
        parts: list[str] = []
        want_foreshadowing = not layer or layer == "伏笔"
        want_layers = None if (not layer or layer == "伏笔") else [layer]
        mem_limit = max(1, limit - 5) if want_foreshadowing else limit

        mem_res = await memory_service.get_spark_ideas_for_prompt(
            bid, query,
            options={"layers": want_layers, "chapterId": cid, "limit": mem_limit},
        )
        if mem_res:
            by_layer: dict[str, list[str]] = {}
            for m in mem_res:
                # 行首带 [id:N] 前缀，便于 LLM 后续调 updateSparkIdea / 引用
                mid = m.get("id")
                mtxt = str(m.get("content") or "").strip()
                line = f"[id:{mid}] {mtxt}" if mid is not None else mtxt
                by_layer.setdefault(m.get("layer", ""), []).append(line)
            for layer_name in SPARK_IDEA_LAYER_ORDERED:
                arr = by_layer.get(layer_name)
                if arr:
                    parts.append(f"【{layer_name}设定】\n" + "\n".join(arr))

        if want_foreshadowing:
            for_res = await memory_service.get_foreshadowing_for_prompt(
                bid, query, options={"limit": 5},
            )
            if for_res:
                lines = [f"- {f.get('content')}（类型：{f.get('type')}，状态：{f.get('status')}）" for f in for_res]
                parts.append("【伏笔】\n" + "\n".join(lines))

        return ToolResult("\n\n".join(parts) if parts else "（未找到与当前检索相关的本书设定）")
    except Exception as e:
        return _err({"error": str(e)})


@tool("searchMemories")
async def _tool_search_memories(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法检索长期记忆"})
    query = str(args.get("query") or "").strip()
    options: dict[str, Any] = {
        "limit": args.get("limit") or 12,
    }
    if args.get("kinds"):
        options["kinds"] = args.get("kinds")
    if args.get("statuses"):
        options["statuses"] = args.get("statuses")
    if args.get("scopeType"):
        options["scopeType"] = args.get("scopeType")
    if args.get("scopeId"):
        options["scopeId"] = args.get("scopeId")
    try:
        from services import long_term_memory_service
        rows = await long_term_memory_service.search_memory_items(bid, query, options=options)
        if not rows:
            return ToolResult("（未找到相关长期记忆）")
        lines = [
            f"[id:{r.get('id')}] [{r.get('kind')}|{r.get('status')}] {str(r.get('content') or '').strip()}"
            for r in rows
        ]
        return ToolResult("\n".join(lines))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("createMemory")
async def _tool_create_memory(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    content = str(args.get("content") or "").strip()
    kind = str(args.get("kind") or "summary").strip()
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建长期记忆"})
    if not content:
        return _err({"success": False, "error": "记忆内容不能为空"})
    try:
        from services import long_term_memory_service
        row = await long_term_memory_service.create_memory_item(
            book_id=bid,
            kind=kind,
            content=content,
            scope_type=args.get("scopeType") or "book",
            scope_id=args.get("scopeId"),
            summary=args.get("summary") or "",
            keywords=args.get("keywords") or "",
            importance=int(args.get("importance") or 3),
            confidence=float(args.get("confidence") or 1.0),
            status=args.get("status") or "active",
            pinned=bool(args.get("pinned") or False),
            source_type="tool",
            source_id=args.get("sourceId"),
        )
        return ToolResult(json.dumps({
            "success": True,
            "id": row.get("id"),
            "deduped": row.get("deduped", False),
            "message": "已保存长期记忆",
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("updateMemory")
async def _tool_update_memory(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    raw_id = args.get("id")
    mid = str(raw_id).strip() if raw_id is not None else ""
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not mid:
        return _err({"success": False, "error": "缺少 id：updateMemory 必须指定要更新的记忆"})
    allowed = {
        "kind": "kind",
        "content": "content",
        "summary": "summary",
        "keywords": "keywords",
        "importance": "importance",
        "confidence": "confidence",
        "status": "status",
        "pinned": "pinned",
        "scopeType": "scope_type",
        "scopeId": "scope_id",
    }
    payload = {dst: args[src] for src, dst in allowed.items() if src in args}
    if not payload:
        return _err({"success": False, "error": "noop：缺少可更新字段", "noop": True})
    try:
        from services import long_term_memory_service
        existing = await long_term_memory_service.get_memory_items_by_ids([mid])
        if not existing or str(existing[0].get("book_id") or "") != str(bid):
            return _err({
                "success": False,
                "error": "该记忆不属于当前书籍，已拒绝更新",
            })
        row = await long_term_memory_service.update_memory_item(mid, payload)
        if not row:
            return _err({"success": False, "error": f"未找到 id={mid} 的长期记忆"})
        return ToolResult(json.dumps({"success": True, "id": row.get("id"), "status": row.get("status")}, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("archiveMemory")
async def _tool_archive_memory(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    raw_id = args.get("id")
    mid = str(raw_id).strip() if raw_id is not None else ""
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not mid:
        return _err({"success": False, "error": "缺少 id：archiveMemory 必须指定要归档的记忆"})
    try:
        from services import long_term_memory_service
        existing = await long_term_memory_service.get_memory_items_by_ids([mid])
        if not existing or str(existing[0].get("book_id") or "") != str(bid):
            return _err({
                "success": False,
                "error": "该记忆不属于当前书籍，已拒绝归档",
            })
        row = await long_term_memory_service.archive_memory_item(mid)
        if not row:
            return _err({"success": False, "error": f"未找到 id={mid} 的长期记忆"})
        return ToolResult(json.dumps({"success": True, "id": row.get("id"), "status": row.get("status")}, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("linkMemories")
async def _tool_link_memories(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法关联长期记忆"})
    try:
        from services import long_term_memory_service
        row = await long_term_memory_service.link_memory_items(
            book_id=bid,
            from_memory_id=args.get("fromMemoryId"),
            to_memory_id=args.get("toMemoryId"),
            relation=str(args.get("relation") or ""),
            note=str(args.get("note") or ""),
        )
        return ToolResult(json.dumps({"success": True, "id": row.get("id")}, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("resolveForeshadowing")
async def _tool_resolve_foreshadowing(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    raw_id = args.get("id")
    fid = str(raw_id).strip() if raw_id is not None else ""
    resolved_chapter_id = args.get("resolvedChapterId") or _runtime_chapter_id(ctx)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId"})
    if not fid:
        return _err({"success": False, "error": "缺少 id：resolveForeshadowing 必须指定伏笔"})
    try:
        from services import memory_service
        existing = await memory_service.get_foreshadowing_by_ids([fid])
        if not existing or str(existing[0].get("book_id") or "") != str(bid):
            return _err({
                "success": False,
                "error": "该伏笔不属于当前书籍，已拒绝更新",
            })
        row = await memory_service.update_foreshadowing(fid, {
            "status": "已回收",
            "resolved_chapter_id": str(resolved_chapter_id) if resolved_chapter_id is not None else None,
        })
        if not row:
            return _err({"success": False, "error": f"未找到 id={fid} 的伏笔"})
        return ToolResult(json.dumps({
            "success": True,
            "id": row.get("id"),
            "status": row.get("status"),
            "resolved_chapter_id": row.get("resolved_chapter_id"),
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})
