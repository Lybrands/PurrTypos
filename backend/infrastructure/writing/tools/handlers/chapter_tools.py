"""Writing Agent 的章节读写 handler。"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Callable

from database.crud.chapters import add_chapter
from database.crud.outlines import get_or_create_writing_outline
from domains.writing.tools.cache import build_read_cache_key
from domains.writing.tools.contracts import ToolResult, _err
from domains.writing.tools.scope import (
    _chapter_node_allowed_by_writing_catalog,
    _reject_numeric_chapter_ids_for_batch,
    _resolve_create_writing_parent_id,
    chapter_allowed_by_writing_catalog,
    chapters_allowed_by_writing_catalog,
    resolve_book_id_for_tools,
    resolve_chapter_id_strict,
)
from domains.writing.tools.state import (
    CATALOG_TOOL_FAIL_MSG,
    _catalog_reject_payload,
    _ensure_chapter_content_cache,
    _invalidate_read_tool_cache,
    _json_batch_catalog_reject,
    _json_catalog_reject,
    _read_tool_cache_get,
    _read_tool_cache_set,
    _runtime_chapter_id,
    _runtime_writing_chapters,
    _title_from_writing_catalog,
)
from infrastructure.writing.tools.data_loaders import (
    _list_writing_chapters,
    _list_writing_chapter_rows_for_book,
    _read_writing_chapter_for_book,
    _read_writing_chapters_for_book,
)

if TYPE_CHECKING:
    from infrastructure.writing.tools.runtime import WritingToolDependencies


async def _tool_get_chapter_content(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    resolved = resolve_chapter_id_strict(
        args,
        writing_chapters,
        _runtime_chapter_id(ctx),
    )
    if not resolved["ok"] or not resolved.get("chapterId"):
        msg = (
            "getChapterContent 仅支持 chapterId"
            if resolved.get("reason") == "non_id_locator_forbidden"
            else CATALOG_TOOL_FAIL_MSG
        )
        return _err({"error": msg})

    chapter_id = resolved["chapterId"]
    title = args.get("title")
    max_length = args.get("maxTextLength") or 12000
    gate = chapter_allowed_by_writing_catalog(chapter_id, writing_chapters)
    if not gate["ok"]:
        return ToolResult(_json_catalog_reject(ctx, gate, chapter_id))

    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"error": CATALOG_TOOL_FAIL_MSG, "chapterId": chapter_id})
    chapter = await _read_writing_chapter_for_book(
        dependencies.db,
        str(book_id),
        chapter_id,
    )
    if not chapter:
        return _err({"error": CATALOG_TOOL_FAIL_MSG, "chapterId": chapter_id})

    key = str(chapter_id).strip()
    cache = _ensure_chapter_content_cache(ctx)
    if cache is not None and key in cache:
        entry = cache[key]
        plain_slice = str(entry.get("plainTextFull") or "")[:max_length]
        title_resolved = (
            title
            or entry.get("titleResolved")
            or _title_from_writing_catalog(chapter_id, writing_chapters)
            or chapter.get("title")
            or ""
        )
        payload = {
            "chapterId": key,
            "title": title_resolved,
            "plainText": plain_slice or "（本章暂无正文内容）",
        }
        return ToolResult(json.dumps(payload, ensure_ascii=False), from_cache=True)

    title_resolved = (
        title
        or _title_from_writing_catalog(chapter_id, writing_chapters)
        or chapter.get("title")
        or ""
    )
    if cache is not None:
        cache[key] = {
            "plainTextFull": chapter["plainTextFull"],
            "titleResolved": title_resolved,
        }
    plain_text = str(chapter.get("plainTextFull") or "")[:max_length]
    payload = {
        "chapterId": key,
        "title": title_resolved,
        "plainText": plain_text or "（本章暂无正文内容）",
    }
    return ToolResult(json.dumps(payload, ensure_ascii=False))


async def _tool_list_writing_chapters(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({
            "success": False,
            "error": "缺少有效 bookId，无法获取写作目录章节列表",
        })

    cache_key = build_read_cache_key("listWritingChapters", ctx, args)
    hit = _read_tool_cache_get(ctx, cache_key) if cache_key else None
    if hit is not None:
        return ToolResult(hit, from_cache=True)

    result = await _list_writing_chapters(dependencies.db, str(book_id))
    from application.continuation_context import ContinuationContextService
    history = await ContinuationContextService(dependencies.db).list_source_sections(book_id=str(book_id), limit=50)
    if history["total"]:
        result["sourceHistory"] = {**history, "readTool": "readContinuationSourceSection", "readOnly": True}
    payload = json.dumps(result, ensure_ascii=False)[:24000]
    if cache_key:
        _read_tool_cache_set(ctx, cache_key, payload)
    return ToolResult(payload)


async def _tool_create_writing_chapter(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return _err({"success": False, "error": "缺少有效 bookId，无法创建章节"})

    writing_chapters = _runtime_writing_chapters(ctx)
    parent_id = _resolve_create_writing_parent_id(
        args.get("parentId"),
        writing_chapters,
        _runtime_chapter_id(ctx),
    )
    if parent_id:
        gate = _chapter_node_allowed_by_writing_catalog(
            parent_id,
            writing_chapters,
        )
        if not gate["ok"]:
            payload = _catalog_reject_payload(ctx, gate, parent_id)
            extra = (
                {"parentId": payload["chapterId"]}
                if payload.get("chapterId")
                else {}
            )
            return _err({"success": False, "error": payload["error"], **extra})

    try:
        db = dependencies.db
        if await db.fetch_one(
            "SELECT id FROM books WHERE id = ?",
            [str(book_id)],
        ) is None:
            return _err({
                "success": False,
                "error": "当前书籍不存在，无法创建章节",
            })
        parent_record = None
        if parent_id:
            parent_record = await _read_writing_chapter_for_book(
                db,
                str(book_id),
                parent_id,
            )
            if not parent_record:
                payload = _catalog_reject_payload(
                    ctx,
                    {"ok": False, "chapterId": parent_id},
                    parent_id,
                )
                extra = (
                    {"parentId": payload["chapterId"]}
                    if payload.get("chapterId")
                    else {}
                )
                return _err({
                    "success": False,
                    "error": payload["error"],
                    **extra,
                })

        writing = await get_or_create_writing_outline(db, str(book_id))
        if not writing or not writing.get("id"):
            return _err({"success": False, "error": "未找到写作目录，无法创建章节"})

        authoritative_chapters = await _list_writing_chapter_rows_for_book(
            db,
            str(book_id),
            str(writing["id"]),
        )
        if parent_id and (
            not parent_record
            or str(parent_record.get("outline_id") or "")
            != str(writing["id"])
            or not any(
                str(chapter.get("id") or "") == str(parent_id)
                for chapter in authoritative_chapters
            )
        ):
            payload = _catalog_reject_payload(
                ctx,
                {"ok": False, "chapterId": parent_id},
                parent_id,
            )
            extra = (
                {"parentId": payload["chapterId"]}
                if payload.get("chapterId")
                else {}
            )
            return _err({
                "success": False,
                "error": payload["error"],
                **extra,
            })

        effective_parent_id = parent_id or None
        inherited_count = await db.fetch_one("SELECT COUNT(*) AS n FROM continuation_source_sections WHERE book_id=? AND section_type='chapter'", [str(book_id)])
        max_number = int((inherited_count or {}).get("n") or 0)
        for chapter in authoritative_chapters:
            row_parent_id = (
                None
                if chapter.get("parent_id") in (None, "")
                else str(chapter["parent_id"])
            )
            if row_parent_id != effective_parent_id:
                continue
            match = re.match(r"^第(\d+)章", str(chapter.get("title") or ""))
            if match:
                max_number = max(max_number, int(match.group(1)))
        new_title = f"第{max_number + 1}章"
        created = await add_chapter(
            db,
            str(writing["id"]),
            new_title,
            effective_parent_id,
        )
        _invalidate_read_tool_cache(ctx)

        new_id = str(created["id"])
        new_title_resolved = str(created.get("title") or new_title)
        new_parent_id = (
            None
            if created.get("parent_id") in (None, "")
            else str(created["parent_id"])
        )

        # 同步运行态，让本批后续工具直接看到新章节。
        new_writing_chapters = [
            chapter
            for chapter in authoritative_chapters
            if str(chapter.get("id", "")) != new_id
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
            send_chunk({
                "chapterCreated": {
                    "chapterId": new_id,
                    "title": new_title_resolved,
                    "parentId": new_parent_id,
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "bookId": str(book_id),
            "writingOutlineId": str(writing["id"]),
            "chapter": {
                "id": new_id,
                "title": new_title_resolved,
                "parentId": new_parent_id,
                "level": int(created.get("level") or 1),
                "sort": int(created.get("sort") or 0),
            },
        }, ensure_ascii=False))
    except Exception as exc:
        return _err({"success": False, "error": str(exc)})


async def _tool_batch_get_chapter_contents(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    writing_chapters = _runtime_writing_chapters(ctx)
    chapter_ids_raw = args.get("chapterIds", [])
    numeric_check = _reject_numeric_chapter_ids_for_batch(chapter_ids_raw)
    if not numeric_check["ok"]:
        return _err({
            "error": (
                "batchGetChapterContents 仅支持 chapterId 字符串数组"
                "（listWritingChapters.items[].id）"
            ),
        })

    chapter_ids = numeric_check.get("ids", [])
    max_length = args.get("maxTextLength") or 12000
    batch_gate = chapters_allowed_by_writing_catalog(
        chapter_ids,
        writing_chapters,
    )
    if not batch_gate["ok"]:
        return ToolResult(_json_batch_catalog_reject(ctx, batch_gate))

    book_id = resolve_book_id_for_tools(ctx, args)
    if not book_id:
        return ToolResult(_json_batch_catalog_reject(
            ctx,
            {"ok": False, "badIds": chapter_ids},
        ))
    scoped_chapters = await _read_writing_chapters_for_book(
        dependencies.db,
        str(book_id),
        chapter_ids,
    )
    missing_ids = [
        chapter_id
        for chapter_id in chapter_ids
        if str(chapter_id).strip() not in scoped_chapters
    ]
    if missing_ids:
        return ToolResult(_json_batch_catalog_reject(
            ctx,
            {"ok": False, "badIds": missing_ids},
        ))

    title_map = {
        str(chapter.get("id")): chapter.get("title")
        for chapter in writing_chapters
    }
    cache = _ensure_chapter_content_cache(ctx)
    batch_all_cached = bool(chapter_ids)
    entries = []
    for chapter_id_raw in chapter_ids:
        chapter_id = str(chapter_id_raw).strip()
        scoped_chapter = scoped_chapters[chapter_id]
        title = title_map.get(chapter_id) or scoped_chapter.get("title")
        if cache is not None and chapter_id in cache:
            entry = cache[chapter_id]
            plain_text = str(entry.get("plainTextFull") or "")[:max_length]
            entries.append({
                "chapterId": chapter_id,
                "title": title or entry.get("titleResolved") or "",
                "plainText": plain_text,
            })
        else:
            batch_all_cached = False
            if not scoped_chapter.get("articleExists"):
                entries.append({
                    "chapterId": chapter_id,
                    "title": title or "",
                    "plainText": "",
                })
            else:
                title_resolved = (
                    title
                    or _title_from_writing_catalog(chapter_id, writing_chapters)
                    or scoped_chapter.get("title")
                    or ""
                )
                if cache is not None:
                    cache[chapter_id] = {
                        "plainTextFull": scoped_chapter["plainTextFull"],
                        "titleResolved": title_resolved,
                    }
                plain_text = str(
                    scoped_chapter.get("plainTextFull") or ""
                )[:max_length]
                entries.append({
                    "chapterId": chapter_id,
                    "title": title_resolved,
                    "plainText": plain_text,
                })

    payload = json.dumps(entries, ensure_ascii=False)[:24000]
    return ToolResult(payload, from_cache=batch_all_cached)


async def _tool_edit_chapter_content(
    dependencies: "WritingToolDependencies",
    ctx: dict,
    args: dict,
    send_chunk: Callable | None,
) -> ToolResult:
    """提交章节 diff，由用户在编辑器接受后再真正写入正文。"""

    writing_chapters = _runtime_writing_chapters(ctx)
    runtime_chapter_id = _runtime_chapter_id(ctx)
    resolved = resolve_chapter_id_strict(
        args,
        writing_chapters,
        runtime_chapter_id,
    )
    chapter_id = resolved.get("chapterId", "") if resolved["ok"] else ""
    new_content = (
        args.get("content", "") if isinstance(args.get("content"), str) else ""
    )
    if not chapter_id:
        msg = (
            "editChapterContent 仅支持 chapterId（chapterTitle/chapterIndex 已禁用）"
            if resolved.get("reason") == "non_id_locator_forbidden"
            else "章节定位失败（仅支持 chapterId）"
        )
        return _err({"success": False, "error": msg})

    edit_gate = chapter_allowed_by_writing_catalog(
        chapter_id,
        writing_chapters,
    )
    if not edit_gate["ok"]:
        payload = _catalog_reject_payload(ctx, edit_gate, chapter_id)
        extra = (
            {"chapterId": payload["chapterId"]}
            if payload.get("chapterId")
            else {}
        )
        return _err({"success": False, "error": payload["error"], **extra})

    try:
        book_id = resolve_book_id_for_tools(ctx, args)
        chapter = (
            await _read_writing_chapter_for_book(
                dependencies.db,
                str(book_id),
                chapter_id,
            )
            if book_id
            else None
        )
        if not chapter:
            payload = _catalog_reject_payload(
                ctx,
                {"ok": False, "chapterId": chapter_id},
                chapter_id,
            )
            extra = (
                {"chapterId": payload["chapterId"]}
                if payload.get("chapterId")
                else {}
            )
            return _err({
                "success": False,
                "error": payload["error"],
                **extra,
            })

        old_plain = chapter.get("plainTextFull") or ""
        old_trimmed = str(old_plain).strip()
        merged_content = new_content

        if (merged_content or "").strip() == old_trimmed and old_trimmed != "":
            return ToolResult(json.dumps({
                "success": True,
                "message": "新内容与现有正文一致，无需变更",
                "chapterId": chapter_id,
                "noop": True,
            }, ensure_ascii=False))

        if send_chunk:
            send_chunk({
                "proposedChapterDiff": {
                    "chapterId": chapter_id,
                    "beforeText": old_plain,
                    "proposedText": merged_content,
                    "source": "ai_tool_edit",
                },
            })
        return ToolResult(json.dumps({
            "success": True,
            "message": "已向用户提交差异预览，需用户在编辑器接受/拒绝后才会写入正文",
            "chapterId": chapter_id,
            "pendingUserApproval": True,
        }, ensure_ascii=False))
    except Exception as exc:
        return _err({"success": False, "error": str(exc)})
