"""Writing chapter tool handlers."""

from __future__ import annotations

import json
import re
from typing import Callable

from database.crud.chapters import add_chapter
from database.crud.outlines import get_or_create_writing_outline
from dependencies import get_db
from services.tool_runtime import (
    CATALOG_TOOL_FAIL_MSG,
    ToolResult,
    _catalog_reject_payload,
    _chapter_node_allowed_by_writing_catalog,
    _ensure_chapter_content_cache,
    _err,
    _invalidate_read_tool_cache,
    _json_batch_catalog_reject,
    _json_catalog_reject,
    _list_writing_chapters,
    _read_chapter_plain_full,
    _read_tool_cache_get,
    _read_tool_cache_set,
    _reject_numeric_chapter_ids_for_batch,
    build_read_cache_key,
    _resolve_create_writing_parent_id,
    _runtime_chapter_id,
    _runtime_writing_chapters,
    _title_from_writing_catalog,
    chapter_allowed_by_writing_catalog,
    chapters_allowed_by_writing_catalog,
    resolve_book_id_for_tools,
    resolve_chapter_id_strict,
    tool,
)


@tool("getChapterContent")
async def _tool_get_chapter_content(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, _runtime_chapter_id(ctx))
    if not resolved["ok"] or not resolved.get("chapterId"):
        msg = "getChapterContent 仅支持 chapterId" if resolved.get("reason") == "non_id_locator_forbidden" else CATALOG_TOOL_FAIL_MSG
        return _err({"error": msg})

    cid = resolved["chapterId"]
    title = args.get("title")
    max_len = args.get("maxTextLength") or 12000
    gate = chapter_allowed_by_writing_catalog(cid, writing_chapters)
    if not gate["ok"]:
        return ToolResult(_json_catalog_reject(ctx, gate, cid))

    key = str(cid).strip()
    cache = _ensure_chapter_content_cache(ctx)
    if cache is not None and key in cache:
        entry = cache[key]
        plain_slice = str(entry.get("plainTextFull") or "")[:max_len]
        title_res = title or entry.get("titleResolved") or _title_from_writing_catalog(cid, writing_chapters) or ""
        payload = {"chapterId": key, "title": title_res, "plainText": plain_slice or "（本章暂无正文内容）"}
        return ToolResult(json.dumps(payload, ensure_ascii=False), from_cache=True)

    full = await _read_chapter_plain_full(cid)
    if not full:
        return _err({"error": CATALOG_TOOL_FAIL_MSG, "chapterId": cid})

    title_resolved = title or _title_from_writing_catalog(cid, writing_chapters) or ""
    if cache is not None:
        cache[key] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_resolved}
    plain_text = str(full.get("plainTextFull") or "")[:max_len]
    payload = {"chapterId": key, "title": title_resolved, "plainText": plain_text or "（本章暂无正文内容）"}
    return ToolResult(json.dumps(payload, ensure_ascii=False))


@tool("listWritingChapters")
async def _tool_list_writing_chapters(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法获取写作目录章节列表"})

    ck = build_read_cache_key("listWritingChapters", ctx, args)
    hit = _read_tool_cache_get(ctx, ck) if ck else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _list_writing_chapters(str(bid))
    payload = json.dumps(result, ensure_ascii=False)[:24000]
    if ck:
        _read_tool_cache_set(ctx, ck, payload)
    return ToolResult(payload)


@tool("createWritingChapter")
async def _tool_create_writing_chapter(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    bid = resolve_book_id_for_tools(ctx, args)
    if not bid:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建章节"})

    writing_chapters = _runtime_writing_chapters(ctx)
    parent_id = _resolve_create_writing_parent_id(args.get("parentId"), writing_chapters, _runtime_chapter_id(ctx))
    if parent_id:
        gate = _chapter_node_allowed_by_writing_catalog(parent_id, writing_chapters)
        if not gate["ok"]:
            p = _catalog_reject_payload(ctx, gate, parent_id)
            extra = {"parentId": p["chapterId"]} if p.get("chapterId") else {}
            return _err({"success": False, "error": p["error"], **extra})

    try:
        db = get_db()
        writing = await get_or_create_writing_outline(db, str(bid))
        if not writing or not writing.get("id"):
            return _err({"success": False, "error": "未找到写作目录，无法创建章节"})

        effective_pid = parent_id or None
        max_num = 0
        for c in writing_chapters:
            pid = None if c.get("parent_id") in (None, "") else str(c["parent_id"])
            if pid != effective_pid:
                continue
            m = re.match(r"^第(\d+)章", str(c.get("title") or ""))
            if m:
                max_num = max(max_num, int(m.group(1)))
        new_title = f"第{max_num + 1}章"
        created = await add_chapter(db, str(writing["id"]), new_title, effective_pid)
        _invalidate_read_tool_cache(ctx)

        new_id = str(created["id"])
        new_title_resolved = str(created.get("title") or new_title)
        new_parent_id = None if created.get("parent_id") in (None, "") else str(created["parent_id"])

        # 同步运行态：让本批后续工具直接看到新章节
        new_writing_chapters = [
            c for c in writing_chapters if str(c.get("id", "")) != new_id
        ] + [{
            "id": new_id,
            "title": new_title_resolved,
            "parent_id": new_parent_id,
            "level": int(created.get("level") or 1),
            "sort": int(created.get("sort") or 0),
        }]
        ctx["chapterId"] = new_id
        ctx["currentChapterTitle"] = new_title_resolved
        ctx["writingChapters"] = new_writing_chapters

        if send_chunk:
            send_chunk({"chapterCreated": {"chapterId": new_id, "title": new_title_resolved, "parentId": new_parent_id}})
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(bid),
            "writingOutlineId": str(writing["id"]),
            "chapter": {
                "id": new_id,
                "title": new_title_resolved,
                "parentId": new_parent_id,
                "level": int(created.get("level") or 1),
                "sort": int(created.get("sort") or 0),
            },
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})


@tool("batchGetChapterContents")
async def _tool_batch_get_chapter_contents(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    cids_raw = args.get("chapterIds", [])
    numeric_check = _reject_numeric_chapter_ids_for_batch(cids_raw)
    if not numeric_check["ok"]:
        return _err({"error": "batchGetChapterContents 仅支持 chapterId 字符串数组（listWritingChapters.items[].id）"})

    cids = numeric_check.get("ids", [])
    max_len = args.get("maxTextLength") or 12000
    batch_gate = chapters_allowed_by_writing_catalog(cids, writing_chapters)
    if not batch_gate["ok"]:
        return ToolResult(_json_batch_catalog_reject(ctx, batch_gate))

    title_map = {str(c.get("id")): c.get("title") for c in writing_chapters}
    cache = _ensure_chapter_content_cache(ctx)
    batch_all_cached = bool(cids)
    entries = []
    for cid_raw in cids:
        cid = str(cid_raw).strip()
        t = title_map.get(cid)
        if cache is not None and cid in cache:
            entry = cache[cid]
            pt = str(entry.get("plainTextFull") or "")[:max_len]
            entries.append({"chapterId": cid, "title": t or entry.get("titleResolved") or "", "plainText": pt})
        else:
            batch_all_cached = False
            full = await _read_chapter_plain_full(cid)
            if not full:
                entries.append({"chapterId": cid, "title": t or "", "plainText": ""})
            else:
                title_res = t or _title_from_writing_catalog(cid, writing_chapters) or ""
                if cache is not None:
                    cache[cid] = {"plainTextFull": full["plainTextFull"], "titleResolved": title_res}
                pt = str(full.get("plainTextFull") or "")[:max_len]
                entries.append({"chapterId": cid, "title": title_res, "plainText": pt})

    payload = json.dumps(entries, ensure_ascii=False)[:24000]
    return ToolResult(payload, from_cache=batch_all_cached)

@tool("editChapterContent")
async def _tool_edit_chapter_content(ctx: dict, args: dict, send_chunk: Callable | None) -> ToolResult:
    """自 v3.1 起 editChapterContent 不再直接落库，改为提交 diff 给前端，
    由用户在 DiffOverlay 接受/拒绝后通过 commitChapterDiff 真正写库。

    返回给 LLM 的语义是"已提交差异，等待用户确认"，**不要**再自称"已保存"，
    否则模型会基于该假设继续动作。
    """
    writing_chapters = _runtime_writing_chapters(ctx)
    runtime_chapter_id = _runtime_chapter_id(ctx)
    resolved = resolve_chapter_id_strict(args, writing_chapters, runtime_chapter_id)
    cid = resolved.get("chapterId", "") if resolved["ok"] else ""
    new_content = args.get("content", "") if isinstance(args.get("content"), str) else ""
    if not cid:
        msg = (
            "editChapterContent 仅支持 chapterId（chapterTitle/chapterIndex 已禁用）"
            if resolved.get("reason") == "non_id_locator_forbidden"
            else "章节定位失败（仅支持 chapterId）"
        )
        return _err({"success": False, "error": msg})

    edit_gate = chapter_allowed_by_writing_catalog(cid, writing_chapters)
    if not edit_gate["ok"]:
        p = _catalog_reject_payload(ctx, edit_gate, cid)
        extra = {"chapterId": p["chapterId"]} if p.get("chapterId") else {}
        return _err({"success": False, "error": p["error"], **extra})

    try:
        old_plain = (await _read_chapter_plain_full(cid) or {}).get("plainTextFull") or ""
        old_trim = str(old_plain).strip()
        merged_content = new_content

        # 与现有正文完全一致时无 diff 可提，告诉 LLM "无需变更" 即可
        if (merged_content or "").strip() == old_trim and old_trim != "":
            return ToolResult(json.dumps({
                "success": True,
                "message": "新内容与现有正文一致，无需变更",
                "chapterId": cid,
                "noop": True,
            }, ensure_ascii=False))

        if send_chunk:
            send_chunk({
                "proposedChapterDiff": {
                    "chapterId": cid,
                    "beforeText": old_plain,
                    "proposedText": merged_content,
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
            "chapterId": cid,
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as e:
        return _err({"success": False, "error": str(e)})
