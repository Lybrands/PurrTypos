"""Persistence helpers for screenplay projects and versioned documents."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.adaptation_brief import normalize_creative_brief
from domains.screenplay.delivery_manifest import build_delivery_manifest
from domains.screenplay.review_trace import (
    build_revision_trace,
    normalize_review_issues,
    normalize_review_verifications,
)
from domains.screenplay.scene_execution import validate_scene_execution_history
from domains.screenplay.scene_trace import normalize_scene_trace
from domains.screenplay.structure_trace import normalize_structure_trace
from exceptions import AppError, NotFoundError
from utils.id_utils import short_id8


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_string_list(value: Any) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _document_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["content_json"] = _json_object(result.get("content_json"))
    result["derived_from_ids"] = _json_string_list(result.get("derived_from_ids"))
    return result


def _project_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    from domains.screenplay.source_scope import parse_source_scope

    result = dict(row)
    result["source_scope"] = parse_source_scope(
        result.pop("source_scope_json", None)
    )
    raw_manifest = result.pop("delivery_manifest_json", None)
    result["delivery_manifest"] = (
        _json_object(raw_manifest) if raw_manifest else None
    )
    return result


async def list_projects(db, *, include_archived: bool = False) -> list[dict[str, Any]]:
    where = "" if include_archived else "WHERE status != 'archived'"
    rows = await db.fetch_all(
        "SELECT * FROM screenplay_projects "
        f"{where} ORDER BY update_time DESC, create_time DESC"
    )
    return [view for row in rows if (view := _project_view(row)) is not None]


async def get_project(db, project_id: str) -> dict[str, Any] | None:
    return _project_view(await db.fetch_one(
        "SELECT * FROM screenplay_projects WHERE id = ?",
        [project_id],
    ))


async def _require_active_project(db, project_id: str) -> dict[str, Any]:
    project = await get_project(db, project_id)
    if project is None:
        raise NotFoundError("剧本项目不存在")
    if project.get("status") == "archived":
        raise AppError("项目已归档，恢复项目后才能修改", 409)
    return project


async def create_project(
    db,
    *,
    title: str,
    source_kind: str,
    source_book_id: str | None,
    screenplay_format: str,
    approach: str,
    premise: str,
    source_scope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_book = None
    if source_kind == "book":
        source_book = await db.fetch_one(
            "SELECT id, title FROM books WHERE id = ?",
            [source_book_id],
        )
        if source_book is None:
            raise NotFoundError("来源书籍不存在")
        from domains.screenplay.source_scope import resolve_source_scope

        resolved_source_scope = await resolve_source_scope(
            db,
            str(source_book_id),
            source_scope,
        )
    else:
        from domains.screenplay.source_scope import parse_source_scope

        resolved_source_scope = parse_source_scope(None)

    project_id = short_id8()
    document_id = short_id8()
    brief_payload = {
        "schemaVersion": 1,
        "projectTitle": title,
        "sourceKind": source_kind,
        "sourceBookId": source_book_id,
        "sourceBookTitle": source_book.get("title") if source_book else None,
        "format": screenplay_format,
        "approach": approach,
        "premise": premise,
        "sourceScope": resolved_source_scope,
    }

    async with db.transaction():
        await db.execute(
            "INSERT INTO screenplay_projects "
            "(id, title, source_kind, source_book_id, format, approach, premise, "
            "source_scope_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                project_id,
                title,
                source_kind,
                source_book_id,
                screenplay_format,
                approach,
                premise,
                _json_dump(resolved_source_scope),
            ],
        )
        await db.execute(
            "INSERT INTO screenplay_documents "
            "(id, project_id, kind, title, content_json, content_text, version, status) "
            "VALUES (?, ?, 'creative_brief', ?, ?, ?, 1, 'draft')",
            [
                document_id,
                project_id,
                "创作简报",
                _json_dump(brief_payload),
                premise,
            ],
        )

    project = await get_project(db, project_id)
    document = await get_document(db, document_id)
    if project is None or document is None:
        raise RuntimeError("剧本项目创建后无法读取")
    return {"project": project, "initialDocument": document}


async def update_project(
    db,
    project_id: str,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    existing = await get_project(db, project_id)
    if existing is None:
        raise NotFoundError("剧本项目不存在")
    column_map = {
        "title": "title",
        "format": "format",
        "approach": "approach",
        "premise": "premise",
        "status": "status",
    }
    assignments: list[str] = []
    params: list[Any] = []
    for key, column in column_map.items():
        if key not in updates or updates[key] is None:
            continue
        value = updates[key]
        if isinstance(value, str):
            value = value.strip()
        assignments.append(f"{column} = ?")
        params.append(value)
    if assignments:
        assignments.append("update_time = CURRENT_TIMESTAMP")
        params.append(project_id)
        await db.execute(
            f"UPDATE screenplay_projects SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
    project = await get_project(db, project_id)
    if project is None:
        raise NotFoundError("剧本项目不存在")
    return project


async def delete_project(db, project_id: str) -> bool:
    if await get_project(db, project_id) is None:
        return False
    async with db.transaction():
        sessions = await db.fetch_all(
            "SELECT id FROM ai_sessions WHERE screenplay_project_id = ?",
            [project_id],
        )
        session_ids = [int(row["id"]) for row in sessions]
        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            await db.execute(
                f"DELETE FROM ai_conversation_summaries "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_conversations "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"UPDATE ai_agent_runs SET session_id = NULL "
                f"WHERE session_id IN ({placeholders})",
                session_ids,
            )
            await db.execute(
                f"DELETE FROM ai_sessions WHERE id IN ({placeholders})",
                session_ids,
            )
        await db.execute(
            "DELETE FROM screenplay_source_refs WHERE project_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM screenplay_documents WHERE project_id = ?",
            [project_id],
        )
        await db.execute(
            "DELETE FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
    return True


async def get_or_create_agent_session(
    db,
    project_id: str,
) -> dict[str, Any]:
    project = await get_project(db, project_id)
    if project is None:
        raise NotFoundError("剧本项目不存在")
    async with db.transaction():
        session = await db.fetch_one(
            "SELECT * FROM ai_sessions "
            "WHERE screenplay_project_id = ? AND scope = 'screenplay' "
            "AND closed = 0 ORDER BY id DESC LIMIT 1",
            [project_id],
        )
        if session is None:
            session_id = await db.execute_and_get_id(
                "INSERT INTO ai_sessions "
                "(title, scope, screenplay_project_id, book_id, chapter_id) "
                "VALUES (?, 'screenplay', ?, ?, NULL)",
                [
                    f"{str(project.get('title') or '剧本项目')} · Agent",
                    project_id,
                    project.get("source_book_id"),
                ],
            )
            session = await db.fetch_one(
                "SELECT * FROM ai_sessions WHERE id = ?",
                [session_id],
            )
    if session is None:
        raise RuntimeError("剧本 Agent 会话创建后无法读取")
    return session


async def list_documents(
    db,
    project_id: str,
    *,
    kind: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if await get_project(db, project_id) is None:
        raise NotFoundError("剧本项目不存在")
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if status:
        clauses.append("status = ?")
        params.append(status)
    rows = await db.fetch_all(
        "SELECT * FROM screenplay_documents "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY create_time ASC, kind ASC, version DESC",
        params,
    )
    return [view for row in rows if (view := _document_view(row)) is not None]


async def get_document(db, document_id: str) -> dict[str, Any] | None:
    return _document_view(await db.fetch_one(
        "SELECT * FROM screenplay_documents WHERE id = ?",
        [document_id],
    ))


async def create_document(
    db,
    *,
    project_id: str,
    kind: str,
    title: str,
    content_json: Mapping[str, Any],
    content_text: str,
    derived_from_ids: Sequence[str],
    source_run_id: str | None = None,
) -> dict[str, Any]:
    await _require_active_project(db, project_id)
    normalized_parents = [str(item) for item in derived_from_ids if str(item).strip()]
    if normalized_parents:
        placeholders = ",".join("?" for _ in normalized_parents)
        row = await db.fetch_one(
            "SELECT COUNT(*) AS c FROM screenplay_documents "
            f"WHERE project_id = ? AND id IN ({placeholders})",
            [project_id, *normalized_parents],
        )
        if not row or int(row["c"]) != len(set(normalized_parents)):
            raise AppError("上游文档不属于当前剧本项目")

    document_id = short_id8()
    async with db.transaction():
        latest = await db.fetch_one(
            "SELECT COALESCE(MAX(version), 0) AS version "
            "FROM screenplay_documents WHERE project_id = ? AND kind = ?",
            [project_id, kind],
        )
        version = int((latest or {}).get("version") or 0) + 1
        await db.execute(
            "INSERT INTO screenplay_documents "
            "(id, project_id, kind, title, content_json, content_text, "
            "version, status, derived_from_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?)",
            [
                document_id,
                project_id,
                kind,
                title.strip(),
                _json_dump(dict(content_json)),
                content_text,
                version,
                _json_dump(normalized_parents),
            ],
        )
        if source_run_id:
            from database.crud.screenplay_source_refs import (
                attach_run_refs_to_document,
            )

            await attach_run_refs_to_document(
                db,
                project_id=project_id,
                document_id=document_id,
                agent_run_id=source_run_id,
            )
        await db.execute(
            "UPDATE screenplay_projects SET update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [project_id],
        )
    document = await get_document(db, document_id)
    if document is None:
        raise RuntimeError("剧本文档创建后无法读取")
    return document


async def update_document(
    db,
    document_id: str,
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    existing = await get_document(db, document_id)
    if existing is None:
        raise NotFoundError("剧本文档不存在")
    await _require_active_project(db, existing["project_id"])
    if existing["status"] != "draft":
        raise AppError("只有草稿文档可以直接修改", 409)

    if updates.get("derivedFromIds") is not None:
        normalized_parents = [
            str(item)
            for item in updates["derivedFromIds"]
            if str(item).strip()
        ]
        if normalized_parents:
            placeholders = ",".join("?" for _ in normalized_parents)
            row = await db.fetch_one(
                "SELECT COUNT(*) AS c FROM screenplay_documents "
                f"WHERE project_id = ? AND id IN ({placeholders})",
                [existing["project_id"], *normalized_parents],
            )
            if not row or int(row["c"]) != len(set(normalized_parents)):
                raise AppError("上游文档不属于当前剧本项目")

    column_map = {
        "title": "title",
        "contentJson": "content_json",
        "contentText": "content_text",
        "derivedFromIds": "derived_from_ids",
    }
    assignments: list[str] = []
    params: list[Any] = []
    for key, column in column_map.items():
        if key not in updates or updates[key] is None:
            continue
        value = updates[key]
        if key in {"contentJson", "derivedFromIds"}:
            value = _json_dump(value)
        elif key == "title":
            value = str(value).strip()
        assignments.append(f"{column} = ?")
        params.append(value)
    if assignments:
        assignments.append("update_time = CURRENT_TIMESTAMP")
        params.append(document_id)
        async with db.transaction():
            await db.execute(
                f"UPDATE screenplay_documents SET {', '.join(assignments)} WHERE id = ?",
                params,
            )
            await db.execute(
                "UPDATE screenplay_projects SET update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [existing["project_id"]],
            )
    document = await get_document(db, document_id)
    if document is None:
        raise NotFoundError("剧本文档不存在")
    return document


async def accept_document(db, document_id: str) -> dict[str, Any]:
    existing = await get_document(db, document_id)
    if existing is None:
        raise NotFoundError("剧本文档不存在")
    await _require_active_project(db, existing["project_id"])
    if existing["status"] == "superseded":
        raise AppError("已被替代的文档不能重新接受", 409)
    if existing["status"] == "accepted":
        return existing
    async with db.transaction():
        existing = await get_document(db, document_id)
        if existing is None:
            raise NotFoundError("剧本文档不存在")
        project = await get_project(db, existing["project_id"])
        if project is None:
            raise NotFoundError("剧本项目不存在")
        next_stage, retire_review = await _validate_document_acceptance(
            db,
            project,
            existing,
        )
        delivery_manifest = (
            await _build_completion_delivery_manifest(
                db,
                project=project,
                final_review=existing,
            )
            if next_stage == "completed"
            else None
        )
        await db.execute(
            "UPDATE screenplay_documents SET status = 'superseded', "
            "update_time = CURRENT_TIMESTAMP "
            "WHERE project_id = ? AND kind = ? AND status = 'accepted' AND id != ?",
            [existing["project_id"], existing["kind"], document_id],
        )
        await db.execute(
            "UPDATE screenplay_documents SET status = 'accepted', "
            "update_time = CURRENT_TIMESTAMP WHERE id = ?",
            [document_id],
        )
        if retire_review:
            await db.execute(
                "UPDATE screenplay_documents SET status = 'superseded', "
                "update_time = CURRENT_TIMESTAMP "
                "WHERE project_id = ? AND kind = 'review' "
                "AND status = 'accepted'",
                [existing["project_id"]],
            )
        if next_stage:
            if delivery_manifest is not None:
                await db.execute(
                    "UPDATE screenplay_projects SET active_stage = ?, "
                    "delivery_manifest_json = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [
                        next_stage,
                        _json_dump(delivery_manifest),
                        existing["project_id"],
                    ],
                )
            else:
                await db.execute(
                    "UPDATE screenplay_projects SET active_stage = ?, "
                    "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                    [next_stage, existing["project_id"]],
                )
        else:
            await db.execute(
                "UPDATE screenplay_projects SET "
                "update_time = CURRENT_TIMESTAMP WHERE id = ?",
                [existing["project_id"]],
            )
    document = await get_document(db, document_id)
    if document is None:
        raise NotFoundError("剧本文档不存在")
    return document


async def _latest_accepted(db, project_id: str, kinds: Sequence[str]):
    placeholders = ",".join("?" for _ in kinds)
    return await db.fetch_one(
        "SELECT * FROM screenplay_documents "
        f"WHERE project_id = ? AND kind IN ({placeholders}) "
        "AND status = 'accepted' ORDER BY update_time DESC, version DESC LIMIT 1",
        [project_id, *kinds],
    )


async def _build_completion_delivery_manifest(
    db,
    *,
    project: Mapping[str, Any],
    final_review: Mapping[str, Any],
) -> dict[str, Any]:
    project_id = str(project["id"])

    async def accepted(kind: str) -> dict[str, Any]:
        row = await _latest_accepted(db, project_id, (kind,))
        view = _document_view(row)
        if view is None:
            raise AppError(f"生成交付清单前缺少已接受的 {kind}", 409)
        return view

    source_analysis = (
        await accepted("source_analysis")
        if project.get("source_kind") == "book"
        else None
    )
    creative_brief = await accepted("creative_brief")
    structure_kind = (
        "episode_outline"
        if str(project.get("format") or "") in {"连续剧", "竖屏短剧"}
        else "beat_sheet"
    )
    structure = await accepted(structure_kind)
    scene_list = await accepted("scene_list")
    final_draft = await accepted("scene_draft")
    final_review_view = dict(final_review)
    document_ids = [
        str(document["id"])
        for document in (
            [
                source_analysis,
                creative_brief,
                structure,
                scene_list,
                final_draft,
                final_review_view,
            ]
            if source_analysis is not None
            else [
                creative_brief,
                structure,
                scene_list,
                final_draft,
                final_review_view,
            ]
        )
    ]
    placeholders = ",".join("?" for _ in document_ids)
    ref_rows = await db.fetch_all(
        "SELECT document_id, COUNT(*) AS count "
        "FROM screenplay_source_refs "
        f"WHERE project_id = ? AND document_id IN ({placeholders}) "
        "GROUP BY document_id",
        [project_id, *document_ids],
    )
    source_ref_counts = {
        str(row["document_id"]): int(row["count"])
        for row in ref_rows
    }
    generated = await db.fetch_one(
        "SELECT CURRENT_TIMESTAMP AS generated_at"
    )
    try:
        return build_delivery_manifest(
            project=project,
            source_analysis=source_analysis,
            creative_brief=creative_brief,
            structure=structure,
            scene_list=scene_list,
            final_draft=final_draft,
            final_review=final_review_view,
            source_ref_counts=source_ref_counts,
            generated_at=str(
                (generated or {}).get("generated_at") or ""
            ),
        )
    except ValueError as error:
        raise AppError(str(error), 409) from error


def _require_parent(document: Mapping[str, Any], parent_id: object, message: str) -> None:
    if str(parent_id or "") not in set(document.get("derived_from_ids") or []):
        raise AppError(message, 409)


async def _validate_document_acceptance(
    db,
    project: Mapping[str, Any],
    document: Mapping[str, Any],
) -> tuple[str | None, bool]:
    """Validate the authoritative workflow transition before mutating state."""

    project_id = str(project["id"])
    stage = str(project.get("active_stage") or "orientation")
    kind = str(document.get("kind") or "")
    content_json = document.get("content_json")
    content = content_json if isinstance(content_json, Mapping) else {}
    text = str(document.get("content_text") or "").strip()

    if stage == "completed":
        raise AppError("项目已完成，不能继续接受新版本", 409)

    expected_structure = (
        "episode_outline"
        if str(project.get("format") or "") in {"连续剧", "竖屏短剧"}
        else "beat_sheet"
    )

    if kind == "source_analysis":
        if (
            stage not in {"orientation", "brief"}
            or project.get("source_kind") != "book"
            or not project.get("source_book_id")
        ):
            raise AppError("当前阶段或原作状态不允许接受范围分析", 409)
        if stage == "brief":
            previous_analysis = await _latest_accepted(
                db, project_id, ("source_analysis",)
            )
            if previous_analysis is None:
                raise AppError("修订范围分析前必须先有已接受版本", 409)
            _require_parent(
                document,
                previous_analysis["id"],
                "新版范围分析必须继承当前已接受版本",
            )
        analysis = content.get("analysis")
        if not isinstance(analysis, Mapping):
            raise AppError("原作范围分析缺少结构化 analysis", 409)
        if not str(analysis.get("narrativeSummary") or "").strip():
            raise AppError("原作范围分析缺少叙事摘要", 409)
        coverage = analysis.get("coverage")
        if not isinstance(coverage, Mapping):
            raise AppError("原作范围分析缺少阅读覆盖信息", 409)
        try:
            selected_chapter_count = int(
                coverage.get("selectedChapterCount")
            )
        except (TypeError, ValueError):
            raise AppError("原作范围分析的章节覆盖数量无效", 409) from None
        read_chapter_ids = {
            str(item).strip()
            for item in (
                coverage.get("readChapterIds")
                if isinstance(coverage.get("readChapterIds"), list)
                else []
            )
            if str(item).strip()
        }
        sampled_chapter_ids = {
            str(item).strip()
            for item in (
                coverage.get("sampledChapterIds")
                if isinstance(coverage.get("sampledChapterIds"), list)
                else []
            )
            if str(item).strip()
        }.difference(read_chapter_ids)
        limitations = [
            str(item).strip()
            for item in (
                coverage.get("limitations")
                if isinstance(coverage.get("limitations"), list)
                else []
            )
            if str(item).strip()
        ]
        if (
            selected_chapter_count < 0
            or (
                len(read_chapter_ids) + len(sampled_chapter_ids)
                > selected_chapter_count
            )
        ):
            raise AppError("原作范围分析的章节覆盖数量无效", 409)
        from domains.screenplay.source_scope import scoped_chapters

        scoped = await scoped_chapters(db, project)
        scoped_chapter_ids = {
            str(chapter["id"]) for chapter in scoped
        }
        if selected_chapter_count != len(scoped_chapter_ids):
            raise AppError("范围分析声明的章节总数与项目锁定范围不一致", 409)
        if not (
            read_chapter_ids | sampled_chapter_ids
        ).issubset(scoped_chapter_ids):
            raise AppError("阅读覆盖声明包含项目改编范围外章节", 409)
        if (
            (
                sampled_chapter_ids
                or selected_chapter_count
                > len(read_chapter_ids) + len(sampled_chapter_ids)
            )
            and not limitations
        ):
            raise AppError("抽样或未覆盖完整改编范围时必须说明分析局限", 409)
        plot_events = analysis.get("plotEvents")
        if not isinstance(plot_events, list) or not plot_events:
            raise AppError("原作范围分析必须包含关键事件", 409)
        evidence = analysis.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise AppError("原作范围分析必须包含来源证据", 409)
        refs = await db.fetch_all(
            "SELECT source_type, source_id FROM screenplay_source_refs "
            "WHERE project_id = ? AND document_id = ?",
            [project_id, document["id"]],
        )
        attached = {
            (str(row.get("source_type") or ""), str(row.get("source_id") or ""))
            for row in refs
        }
        cited: set[tuple[str, str]] = set()
        for item in evidence:
            if not isinstance(item, Mapping):
                raise AppError("原作范围分析包含无效来源证据", 409)
            source_type = str(item.get("sourceType") or "").strip()
            source_id = str(item.get("sourceId") or "").strip()
            claim = str(item.get("claim") or "").strip()
            if not source_type or not source_id or not claim:
                raise AppError("每条来源证据都必须包含类型、ID 和事实说明", 409)
            cited.add((source_type, source_id))
        if not cited.issubset(attached):
            raise AppError("原作范围分析引用了本次 Agent 未实际读取的素材", 409)
        attached_chapter_ids = {
            source_id
            for source_type, source_id in attached
            if source_type == "chapter"
        }
        if not (
            read_chapter_ids | sampled_chapter_ids
        ).issubset(attached_chapter_ids):
            raise AppError("阅读覆盖声明包含本次 Agent 未实际读取的章节", 409)
        if not text:
            raise AppError("原作范围分析正文不能为空", 409)
        return ("brief" if stage == "orientation" else None), False

    if kind == "creative_brief":
        is_book_adaptation = project.get("source_kind") == "book"
        allowed_stages = (
            {"brief", "structure"}
            if is_book_adaptation
            else {"orientation", "brief", "structure"}
        )
        if stage not in allowed_stages:
            raise AppError("当前阶段不能接受创作简报", 409)
        source_analysis = None
        if is_book_adaptation:
            source_analysis = await _latest_accepted(
                db, project_id, ("source_analysis",)
            )
            if source_analysis is None:
                raise AppError("接受改编创作简报前必须先接受原作范围分析", 409)
        if is_book_adaptation and stage == "brief":
            _require_parent(
                document,
                source_analysis["id"],
                "改编创作简报必须继承当前原作范围分析",
            )
        if stage == "structure":
            downstream = await _latest_accepted(
                db, project_id, ("beat_sheet", "episode_outline")
            )
            if downstream is not None:
                raise AppError("已有被接受的结构版本，不能直接替换创作简报", 409)
            previous = await _latest_accepted(db, project_id, ("creative_brief",))
            if previous is not None:
                _require_parent(
                    document,
                    previous["id"],
                    "新版创作简报必须继承当前已接受版本",
                )
        if is_book_adaptation:
            if str(content.get("sourceAnalysisId") or "") != str(
                source_analysis["id"]
            ):
                raise AppError("改编创作简报与当前原作范围分析版本不匹配", 409)
            brief = content.get("brief")
            if not isinstance(brief, Mapping):
                raise AppError("改编创作简报缺少结构化改编方案", 409)
            try:
                normalize_creative_brief(
                    brief,
                    project_format=str(project.get("format") or ""),
                    source_analysis=_json_object(
                        source_analysis.get("content_json")
                    ),
                )
            except ValueError as error:
                raise AppError(str(error), 409) from error
        if not text and not content:
            raise AppError("创作简报内容不能为空", 409)
        return ("structure" if stage in {"orientation", "brief"} else None), False

    if kind in {"beat_sheet", "episode_outline"}:
        if stage != "structure" or kind != expected_structure:
            raise AppError("文档类型与当前结构阶段或项目形态不匹配", 409)
        brief = await _latest_accepted(db, project_id, ("creative_brief",))
        if brief is None:
            raise AppError("接受结构前必须先接受创作简报", 409)
        _require_parent(document, brief["id"], "结构版本必须继承当前创作简报")
        if str(content.get("creativeBriefId") or "") != str(brief["id"]):
            raise AppError("结构版本与当前创作简报版本不匹配", 409)
        units_key = "beats" if kind == "beat_sheet" else "episodes"
        try:
            normalize_structure_trace(
                kind=kind,
                units=content.get(units_key),
                decision_coverage=content.get("decisionCoverage"),
                creative_brief=_json_object(brief.get("content_json")),
                require_adaptation_decisions=(
                    project.get("source_kind") == "book"
                ),
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        if not text:
            raise AppError("结构版本正文不能为空", 409)
        return "scenes", False

    if kind == "scene_list":
        if stage != "scenes":
            raise AppError("当前阶段不能接受场景表", 409)
        structure = await _latest_accepted(
            db, project_id, ("beat_sheet", "episode_outline")
        )
        if structure is None:
            raise AppError("接受场景表前必须先接受结构版本", 409)
        _require_parent(document, structure["id"], "场景表必须继承当前结构版本")
        if str(content.get("structureId") or "") != str(structure["id"]):
            raise AppError("场景表与当前结构版本不匹配", 409)
        try:
            normalize_scene_trace(
                scenes=content.get("scenes"),
                structure_kind=str(structure["kind"]),
                structure_content=_json_object(
                    structure.get("content_json")
                ),
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        return "draft", False

    if kind == "scene_draft" and stage == "draft":
        scene_list = await _latest_accepted(db, project_id, ("scene_list",))
        if scene_list is None:
            raise AppError("接受正文前必须先接受场景表", 409)
        _require_parent(document, scene_list["id"], "正文必须继承当前场景表")
        if str(content.get("sceneListId") or "") != str(scene_list["id"]):
            raise AppError("正文与当前场景表版本不匹配", 409)
        scene_json = _json_object(scene_list.get("content_json"))
        scene_rows = [
            item
            for item in scene_json.get("scenes", [])
            if isinstance(item, Mapping)
        ]
        scene_ids = [
            str(item.get("id") or "").strip()
            for item in scene_rows
        ]
        completed = content.get("completedSceneIds")
        if not isinstance(completed, list):
            raise AppError("正文必须声明 completedSceneIds", 409)
        completed_ids = [str(item) for item in completed]
        if completed_ids != scene_ids[:len(completed_ids)]:
            raise AppError("正文完成场景必须严格遵循场景表顺序", 409)
        if (
            not completed_ids
            or str(content.get("sceneId") or "") != completed_ids[-1]
        ):
            raise AppError("正文 sceneId 必须是本次新增完成的场景", 409)
        current_scene = next(
            (
                scene for scene in scene_rows
                if str(scene.get("id") or "") == completed_ids[-1]
            ),
            None,
        )
        if (
            current_scene is None
            or str(content.get("sceneHeading") or "").strip()
            != str(current_scene.get("heading") or "").strip()
        ):
            raise AppError("正文 sceneHeading 必须与场景表一致", 409)
        latest_draft = await _latest_accepted(db, project_id, ("scene_draft",))
        previous_completed: list[str] = []
        previous_executions: list[Any] = []
        if latest_draft is not None:
            _require_parent(document, latest_draft["id"], "滚动正文必须继承当前已接受正文")
            previous_json = _json_object(latest_draft.get("content_json"))
            if str(previous_json.get("sceneListId") or "") != str(
                scene_list["id"]
            ):
                raise AppError("上一版正文没有绑定当前场景表", 409)
            previous_completed = [
                str(item) for item in previous_json.get("completedSceneIds", [])
            ]
            previous_executions = (
                previous_json.get("sceneExecutions")
                if isinstance(previous_json.get("sceneExecutions"), list)
                else []
            )
        if completed_ids != [*previous_completed, completed_ids[-1]]:
            raise AppError("滚动正文每次必须严格追加一个新场景", 409)
        try:
            validate_scene_execution_history(
                scene_list_content=scene_json,
                completed_scene_ids=completed_ids,
                scene_executions=content.get("sceneExecutions"),
                previous_executions=previous_executions,
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        if not text:
            raise AppError("剧本正文不能为空", 409)
        is_complete = content.get("isComplete") is True
        if is_complete != (completed_ids == scene_ids):
            raise AppError("isComplete 必须与场景完成情况一致", 409)
        return ("review" if is_complete else None), False

    if kind == "review" and stage == "review":
        draft = await _latest_accepted(db, project_id, ("scene_draft",))
        if draft is None or _json_object(draft.get("content_json")).get("isComplete") is not True:
            raise AppError("审阅报告必须针对当前完整剧本", 409)
        draft_json = _json_object(draft.get("content_json"))
        if str(content.get("reviewedDraftId") or "") != str(draft["id"]):
            raise AppError("审阅报告与当前完整剧本版本不匹配", 409)
        _require_parent(document, draft["id"], "审阅报告必须继承当前完整剧本")
        verdict = str(content.get("verdict") or "")
        if verdict not in {"ready", "revise", "major_rework"}:
            raise AppError("审阅结论必须是 ready、revise 或 major_rework", 409)
        try:
            issues = normalize_review_issues(
                issues=content.get("issues"),
                completed_scene_ids=draft_json.get("completedSceneIds"),
                scene_executions=draft_json.get("sceneExecutions"),
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        if content.get("issues") != issues:
            raise AppError("审阅问题必须使用标准化的结构化格式", 409)
        verification_results: list[dict[str, Any]] = []
        previous_review_id = str(draft_json.get("reviewId") or "").strip()
        revision_of = str(draft_json.get("revisionOf") or "").strip()
        if previous_review_id or revision_of:
            if not previous_review_id or not revision_of:
                raise AppError("当前修订稿缺少完整的审阅来源链", 409)
            previous_review = await get_document(db, previous_review_id)
            previous_review_json = (
                previous_review.get("content_json")
                if previous_review is not None
                and previous_review.get("project_id") == project_id
                and previous_review.get("kind") == "review"
                else {}
            )
            if (
                not isinstance(previous_review_json, Mapping)
                or str(previous_review_json.get("reviewedDraftId") or "")
                != revision_of
            ):
                raise AppError("当前修订稿无法追溯到上一轮审阅", 409)
            try:
                verification_results = normalize_review_verifications(
                    previous_review_issues=previous_review_json.get("issues"),
                    issue_resolutions=draft_json.get("issueResolutions"),
                    verification_results=content.get("verificationResults"),
                )
            except ValueError as error:
                raise AppError(str(error), 409) from error
            verified_issue_ids = [
                item["issueId"]
                for item in verification_results
                if item["status"] == "verified"
            ]
            failed_verification_issue_ids = [
                item["issueId"]
                for item in verification_results
                if item["status"] != "verified"
            ]
            if (
                str(content.get("verificationOfReviewId") or "")
                != previous_review_id
                or content.get("verificationResults")
                != verification_results
                or content.get("verifiedIssueIds") != verified_issue_ids
                or content.get("failedVerificationIssueIds")
                != failed_verification_issue_ids
            ):
                raise AppError("复审核验记录与上一轮审阅不一致", 409)
            current_issue_ids = {str(item["id"]) for item in issues}
            if not set(failed_verification_issue_ids).issubset(
                current_issue_ids
            ):
                raise AppError("复审未通过的问题必须延续到当前 issues", 409)
            if set(verified_issue_ids) & current_issue_ids:
                raise AppError("已验证解决的问题不能继续列为待修订问题", 409)
        elif content.get("verificationResults"):
            raise AppError("非修订稿审阅不能包含复审核验结果", 409)
        if verdict == "ready" and issues:
            raise AppError("ready 审阅不能保留待解决问题", 409)
        if verdict != "ready" and not issues:
            raise AppError("要求修订的审阅必须至少包含一个问题", 409)
        if (
            verdict == "ready"
            and any(
                item["status"] != "verified"
                for item in verification_results
            )
        ):
            raise AppError("ready 复审必须验证上一轮全部问题均已解决", 409)
        return ("completed" if verdict == "ready" else None), False

    if kind == "scene_draft" and stage == "review":
        draft = await _latest_accepted(db, project_id, ("scene_draft",))
        review = await _latest_accepted(db, project_id, ("review",))
        if draft is None or review is None:
            raise AppError("修订完整剧本前必须有当前正文和已接受审阅", 409)
        review_json = _json_object(review.get("content_json"))
        if (
            str(review_json.get("reviewedDraftId") or "") != str(draft["id"])
            or str(review_json.get("verdict") or "") == "ready"
        ):
            raise AppError("已接受审阅不适用于当前剧本版本", 409)
        if (
            str(content.get("revisionOf") or "") != str(draft["id"])
            or str(content.get("reviewId") or "") != str(review["id"])
            or content.get("isComplete") is not True
        ):
            raise AppError("修订稿必须明确绑定当前正文和审阅报告", 409)
        draft_json = _json_object(draft.get("content_json"))
        if (
            str(content.get("sceneListId") or "")
            != str(draft_json.get("sceneListId") or "")
            or content.get("completedSceneIds")
            != draft_json.get("completedSceneIds")
        ):
            raise AppError("完整修订稿不能丢失场景执行追踪", 409)
        scene_list = await _latest_accepted(db, project_id, ("scene_list",))
        if (
            scene_list is None
            or str(scene_list["id"])
            != str(draft_json.get("sceneListId") or "")
        ):
            raise AppError("修订稿与当前场景表版本不匹配", 409)
        scene_list_json = _json_object(scene_list.get("content_json"))
        reassessed_scene_ids = content.get("reassessedSceneIds")
        if not isinstance(reassessed_scene_ids, list):
            raise AppError("修订稿必须声明 reassessedSceneIds", 409)
        reassessed = {str(item) for item in reassessed_scene_ids}
        execution_updates = [
            execution
            for execution in content.get("sceneExecutions", [])
            if isinstance(execution, Mapping)
            and str(execution.get("sceneId") or "") in reassessed
        ] if isinstance(content.get("sceneExecutions"), list) else []
        try:
            (
                normalized_resolutions,
                normalized_executions,
                normalized_reassessed_ids,
            ) = build_revision_trace(
                scene_list_content=scene_list_json,
                completed_scene_ids=draft_json.get("completedSceneIds"),
                previous_executions=draft_json.get("sceneExecutions"),
                review_issues=review_json.get("issues"),
                issue_resolutions=content.get("issueResolutions"),
                execution_updates=execution_updates,
            )
        except ValueError as error:
            raise AppError(str(error), 409) from error
        resolved_issue_ids = [
            item["issueId"]
            for item in normalized_resolutions
            if item["status"] == "resolved"
        ]
        partially_resolved_issue_ids = [
            item["issueId"]
            for item in normalized_resolutions
            if item["status"] == "partially_resolved"
        ]
        if (
            content.get("issueResolutions") != normalized_resolutions
            or content.get("sceneExecutions") != normalized_executions
            or content.get("reassessedSceneIds") != normalized_reassessed_ids
            or content.get("resolvedIssueIds") != resolved_issue_ids
            or content.get("partiallyResolvedIssueIds")
            != partially_resolved_issue_ids
        ):
            raise AppError("修订稿的问题回写或场景重评记录不一致", 409)
        _require_parent(document, draft["id"], "修订稿必须继承当前完整剧本")
        _require_parent(document, review["id"], "修订稿必须继承当前审阅报告")
        if not text:
            raise AppError("修订稿正文不能为空", 409)
        return None, True

    raise AppError("文档类型与当前剧本阶段不匹配", 409)


async def delete_document(db, document_id: str) -> bool:
    existing = await get_document(db, document_id)
    if existing is None:
        return False
    await _require_active_project(db, existing["project_id"])
    if existing["status"] != "draft":
        raise AppError("只有草稿文档可以删除", 409)
    async with db.transaction():
        await db.execute(
            "DELETE FROM screenplay_source_refs WHERE document_id = ?",
            [document_id],
        )
        await db.execute(
            "DELETE FROM screenplay_documents WHERE id = ?",
            [document_id],
        )
    return True


async def restore_document(db, document_id: str) -> dict[str, Any]:
    """Restore an immutable historical document by creating a new draft version."""

    existing = await get_document(db, document_id)
    if existing is None:
        raise NotFoundError("剧本文档不存在")
    await _require_active_project(db, existing["project_id"])

    suffix = f"（恢复自 v{existing['version']}）"
    restored_title = f"{str(existing['title'])[:160 - len(suffix)]}{suffix}"
    async with db.transaction():
        restored = await create_document(
            db,
            project_id=existing["project_id"],
            kind=existing["kind"],
            title=restored_title,
            content_json=existing["content_json"],
            content_text=existing["content_text"],
            derived_from_ids=[existing["id"]],
        )
        refs = await db.fetch_all(
            "SELECT agent_run_id, tool_name, source_type, source_id, "
            "source_revision, excerpt FROM screenplay_source_refs "
            "WHERE document_id = ? ORDER BY id ASC",
            [existing["id"]],
        )
        for ref in refs:
            await db.execute(
                "INSERT INTO screenplay_source_refs "
                "(project_id, document_id, agent_run_id, tool_name, "
                "source_type, source_id, source_revision, excerpt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    existing["project_id"],
                    restored["id"],
                    f"restore:{restored['id']}:{ref['agent_run_id']}",
                    ref["tool_name"],
                    ref["source_type"],
                    ref["source_id"],
                    ref["source_revision"],
                    ref["excerpt"],
                ],
            )
    return restored
