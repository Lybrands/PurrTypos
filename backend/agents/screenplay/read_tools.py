"""Bounded product reads for the Screenplay replacement profile."""

from __future__ import annotations

import hashlib
import json

from agents.screenplay.access_receipts import (
    ScreenplayAccessKind,
    ScreenplayOperationAccessReceiptStore,
)
from agents.screenplay.candidate_artifact import (
    SCREENPLAY_CANDIDATE_REF_PREFIX,
    ScreenplayCandidateArtifactStore,
)
from agents.screenplay.capture_artifact import (
    SCREENPLAY_CAPTURE_REF_PREFIX,
    ScreenplayCaptureArtifactStore,
)
from agents.screenplay.contracts import ScreenplayPartOperationScope
from agents.screenplay.host_result_artifact import (
    SCREENPLAY_HOST_RESULT_REF_PREFIX,
    ScreenplayHostResultArtifactStore,
)

from purra.cancellation import raise_if_stopped
from purra.contracts import (
    ToolDataContract,
    ToolExecutionMode,
    ToolHandlerResult,
    ToolPolicy,
    ToolRiskLevel,
    ToolSchema,
)
from purra.ports import ToolRegistration
from purra.json_values import canonical_json_digest
from utils.text import extract_text_from_lexical


SCREENPLAY_ROOT_SCOPE_STATE_KEY = "screenplayRootScope"
SCREENPLAY_OPERATION_SCOPE_STATE_KEY = "screenplayOperationScope"


def build_screenplay_replacement_read_registrations(db):
    candidates = ScreenplayCandidateArtifactStore(db)
    captures = ScreenplayCaptureArtifactStore(db)
    host_results = ScreenplayHostResultArtifactStore(db)
    receipts = ScreenplayOperationAccessReceiptStore(db)

    async def load_dependency(*, project_id, task_id, output_ref):
        stores = (
            (SCREENPLAY_CANDIDATE_REF_PREFIX, candidates),
            (SCREENPLAY_CAPTURE_REF_PREFIX, captures),
            (SCREENPLAY_HOST_RESULT_REF_PREFIX, host_results),
        )
        for prefix, store in stores:
            if output_ref.startswith(prefix):
                return await store.load_dependency(
                    project_id=project_id,
                    task_id=task_id,
                    output_ref=output_ref,
                )
        raise ValueError("Screenplay dependency output type is unsupported")

    async def inspect_project(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        raw_operation_scope = state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY)
        operation_scope = (
            _mapping(raw_operation_scope)
            if raw_operation_scope is not None
            else None
        )
        scope = operation_scope or _mapping(
            state.domain.get(SCREENPLAY_ROOT_SCOPE_STATE_KEY)
        )
        project_id = _text(scope.get("projectId"), "projectId")
        project = await db.fetch_one(
            "SELECT id, title, source_kind, format, approach, premise, "
            "active_stage, status, revision FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
        if project is None:
            return _error("screenplay_project_not_found")
        if operation_scope is not None:
            frozen = _mapping(operation_scope.get("deliverableRevisionScope"))
            accepted_revisions = {
                str(role): str(revision_id)
                for role, revision_id in frozen.items()
                if str(role).strip() and str(revision_id).strip()
            }
        else:
            heads = await db.fetch_all(
                "SELECT d.role, h.revision_id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ? ORDER BY d.role",
                [project_id],
            )
            accepted_revisions = {
                str(row["role"]): str(row["revision_id"]) for row in heads
            }
        return _result({
            "project": dict(project),
            "acceptedRevisions": accepted_revisions,
        })

    async def read_bound_revision(state, arguments, signal=None):
        raise_if_stopped(signal)
        scope = _mapping(state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY))
        project_id = _text(scope.get("projectId"), "projectId")
        role = _text(arguments.get("role"), "role")
        revisions = _mapping(scope.get("deliverableRevisionScope"))
        revision_id = str(revisions.get(role) or "").strip()
        if not revision_id:
            return _error("screenplay_revision_outside_operation_scope")
        revision = await db.fetch_one(
            "SELECT r.id, r.revision_no, r.content_digest, "
            "substr(r.summary_json, 1, 8001) AS summary_json, "
            "length(r.summary_json) AS summary_length "
            "FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
            [revision_id, project_id, role],
        )
        if revision is None:
            return _error("screenplay_revision_scope_invalid")
        rows = await db.fetch_all(
            "SELECT part_type, part_key, position, "
            "substr(payload_json, 1, 8001) AS payload_json, "
            "length(payload_json) AS payload_length, "
            "substr(content_text, 1, 12001) AS content_text, "
            "length(content_text) AS content_length, content_digest "
            "FROM screenplay_revision_parts "
            "WHERE revision_id = ? ORDER BY part_type, position LIMIT 101",
            [revision_id],
        )
        remaining = 48_000
        parts = []
        for row in rows[:100]:
            content = str(row.get("content_text") or "")
            visible = content[: min(12_000, remaining)]
            remaining -= len(visible)
            parts.append({
                "type": str(row["part_type"]),
                "key": str(row["part_key"]),
                "position": int(row["position"]),
                "payload": _bounded_json(
                    row.get("payload_json"),
                    full_length=int(row.get("payload_length") or 0),
                    limit=8_000,
                ),
                "contentText": visible,
                "contentDigest": str(row["content_digest"]),
                "contentTruncated": len(visible) < int(
                    row.get("content_length") or 0
                ),
            })
            if remaining <= 0:
                break
        await _record_access(
            receipts,
            state=state,
            scope=scope,
            access_kind=ScreenplayAccessKind.REVISION,
            resource_ref="screenplay-revision-v1://" + revision_id,
            content_digest=str(revision["content_digest"]),
            metadata={"role": role},
        )
        return _result({
            "revision": {
                "id": str(revision["id"]),
                "role": role,
                "revisionNo": int(revision["revision_no"]),
                "contentDigest": str(revision["content_digest"]),
                "summary": _bounded_json(
                    revision.get("summary_json"),
                    full_length=int(revision.get("summary_length") or 0),
                    limit=8_000,
                ),
            },
            "parts": parts,
            "partsTruncated": len(rows) > len(parts),
        })

    async def list_source_items(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        scope = _mapping(state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY))
        if scope.get("sourceBookId") is None and scope.get("sourceItems") == []:
            return _result({"sourceBookId": None, "items": []})
        book_id = _text(scope.get("sourceBookId"), "sourceBookId")
        items = _source_items(scope)
        chapter_ids = [item["sourceId"] for item in items]
        marks = ",".join("?" for _ in chapter_ids)
        rows = await db.fetch_all(
            "SELECT oc.id, oc.title FROM outline_chapters AS oc "
            "JOIN outlines AS o ON o.id = oc.outline_id "
            f"WHERE o.book_id = ? AND oc.id IN ({marks})",
            [book_id, *chapter_ids],
        )
        titles = {str(row["id"]): str(row.get("title") or "") for row in rows}
        if set(titles) != set(chapter_ids):
            return _error("screenplay_source_scope_invalid")
        return _result({
            "sourceBookId": book_id,
            "items": [{
                "sourceType": item["sourceType"],
                "sourceId": item["sourceId"],
                "title": titles[item["sourceId"]],
                "contentDigest": item["contentDigest"],
            } for item in items],
        })

    async def read_source_item(state, arguments, signal=None):
        raise_if_stopped(signal)
        scope = _mapping(state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY))
        book_id = _text(scope.get("sourceBookId"), "sourceBookId")
        source_id = _text(arguments.get("sourceId"), "sourceId")
        by_id = {item["sourceId"]: item for item in _source_items(scope)}
        item = by_id.get(source_id)
        if item is None:
            return _error("screenplay_source_outside_operation_scope")
        row = await db.fetch_one(
            "SELECT oc.title, a.content FROM outline_chapters AS oc "
            "JOIN outlines AS o ON o.id = oc.outline_id "
            "LEFT JOIN articles AS a ON a.chapter_id = oc.id "
            "WHERE o.book_id = ? AND oc.id = ?",
            [book_id, source_id],
        )
        if row is None:
            return _error("screenplay_source_scope_invalid")
        raw = str(row.get("content") or "")
        text = extract_text_from_lexical(raw) if raw else ""
        digest = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != item["contentDigest"]:
            return _error("screenplay_source_revision_changed")
        await _record_access(
            receipts,
            state=state,
            scope=scope,
            access_kind=ScreenplayAccessKind.SOURCE_ITEM,
            resource_ref=f"novel-source-chapter-v1://{book_id}/{source_id}",
            content_digest=digest,
            metadata={"sourceType": "chapter", "sourceId": source_id},
        )
        return _result({
            "sourceType": "chapter",
            "sourceId": source_id,
            "title": str(row.get("title") or ""),
            "contentDigest": digest,
            "content": text[:48_000],
            "contentTruncated": len(text) > 48_000,
        })

    async def read_dependencies(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        scope = _mapping(state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY))
        project_id = _text(scope.get("projectId"), "projectId")
        task_id = _text(scope.get("taskId"), "taskId")
        keys = scope.get("dependencyPartKeys")
        if not isinstance(keys, list) or not keys:
            return _result({"dependencies": []})
        marks = ",".join("?" for _ in keys)
        rows = await db.fetch_all(
            "SELECT unit_id, semantic_key, status, output_ref "
            "FROM ai_agent_long_task_units WHERE task_id = ? "
            f"AND semantic_key IN ({marks})",
            [task_id, *keys],
        )
        by_key = {str(row["semantic_key"]): row for row in rows}
        if set(by_key) != set(keys):
            return _error("screenplay_dependency_scope_invalid")
        dependencies = []
        for key in keys:
            row = by_key[key]
            if row.get("status") != "completed":
                return _error("screenplay_dependency_not_completed")
            try:
                payload = await load_dependency(
                    project_id=project_id,
                    task_id=task_id,
                    output_ref=str(row.get("output_ref") or ""),
                )
            except ValueError:
                return _error("screenplay_dependency_scope_invalid")
            await _record_access(
                receipts,
                state=state,
                scope=scope,
                access_kind=ScreenplayAccessKind.DEPENDENCY,
                resource_ref=str(row.get("output_ref") or ""),
                content_digest=canonical_json_digest(payload),
                metadata={"partKey": key},
            )
            dependencies.append({"partKey": key, "candidate": payload})
        encoded = json.dumps(dependencies, ensure_ascii=False, allow_nan=False)
        if len(encoded) > 100_000:
            return _error("screenplay_dependency_payload_too_large")
        return _result({"dependencies": dependencies})

    async def read_scene_scope(state, arguments, signal=None):
        del arguments
        raise_if_stopped(signal)
        scope = _mapping(state.domain.get(SCREENPLAY_OPERATION_SCOPE_STATE_KEY))
        project_id = _text(scope.get("projectId"), "projectId")
        scene_id = _text(scope.get("sceneId"), "sceneId")
        episode_number = scope.get("episodeNumber")
        if type(episode_number) is not int or episode_number < 1:
            return _error("screenplay_scene_scope_invalid")
        revisions = _mapping(scope.get("deliverableRevisionScope"))
        scene_list_id = str(revisions.get("sceneList") or "").strip()
        if not scene_list_id:
            return _error("screenplay_scene_scope_invalid")
        episode = await _bound_episode(
            db,
            project_id=project_id,
            revision_id=scene_list_id,
            role="sceneList",
            episode_number=episode_number,
        )
        if episode is None:
            return _error("screenplay_scene_scope_invalid")
        scenes = episode.get("scenes")
        matches = [
            dict(item) for item in scenes or ()
            if isinstance(item, dict) and str(item.get("id") or "").strip() == scene_id
        ] if isinstance(scenes, list) else []
        if len(matches) != 1:
            return _error("screenplay_scene_scope_invalid")
        draft_revision_id = str(revisions.get("screenplayDraft") or "").strip()
        current_draft = None
        previous_episode = None
        if draft_revision_id:
            current = await _bound_episode(
                db,
                project_id=project_id,
                revision_id=draft_revision_id,
                role="screenplayDraft",
                episode_number=episode_number,
            )
            previous_episode = await _bound_episode(
                db,
                project_id=project_id,
                revision_id=draft_revision_id,
                role="screenplayDraft",
                episode_number=episode_number - 1,
            ) if episode_number > 1 else None
            if current is not None:
                current_draft = _scene_draft(current, scene_id)
        for role, revision_id in (
            ("sceneList", scene_list_id),
            ("screenplayDraft", draft_revision_id),
        ):
            if not revision_id:
                continue
            digest = await _bound_revision_digest(
                db,
                project_id=project_id,
                revision_id=revision_id,
                role=role,
            )
            if digest is None:
                return _error("screenplay_scene_scope_invalid")
            await _record_access(
                receipts,
                state=state,
                scope=scope,
                access_kind=ScreenplayAccessKind.REVISION,
                resource_ref="screenplay-revision-v1://" + revision_id,
                content_digest=digest,
                metadata={"role": role},
            )
        return _result({
            "sceneListRevisionId": scene_list_id,
            "draftRevisionId": draft_revision_id or None,
            "episodeNumber": episode_number,
            "sceneId": scene_id,
            "episodePlan": {key: value for key, value in episode.items() if key != "scenes"},
            "scenePlan": matches[0],
            "previousEpisode": previous_episode,
            "currentSceneDraft": current_draft,
        })

    return (
        _registration(
            "inspectScreenplayProjectV1",
            "读取当前剧本项目元数据与已接受版本目录，不返回版本正文。",
            "查看剧本项目",
            {},
            (),
            inspect_project,
            (
                SCREENPLAY_ROOT_SCOPE_STATE_KEY,
                SCREENPLAY_OPERATION_SCOPE_STATE_KEY,
            ),
        ),
        _registration(
            "readScreenplayBoundRevisionV1",
            "按角色读取本次 Operation 已冻结的版本；模型不能指定 revisionId。",
            "读取冻结剧本版本",
            {"role": {"type": "string", "minLength": 1, "maxLength": 80}},
            ("role",),
            read_bound_revision,
            (SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
        _registration(
            "readScreenplayPartDependenciesV1",
            "读取当前 Unit 明确声明的直接上游 Candidate；不能请求其他 Part。",
            "读取剧本任务依赖",
            {},
            (),
            read_dependencies,
            (SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
        _registration(
            "readScreenplaySceneScopeV1",
            "读取当前 draft_scene Unit 主机冻结的场景计划与连续性上下文；不接受场景或版本参数。",
            "读取冻结场景范围",
            {},
            (),
            read_scene_scope,
            (SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
        _registration(
            "listScreenplaySourceItemsV1",
            "列出本次 Operation 冻结的原作来源项，不返回正文。",
            "列出冻结原作来源",
            {},
            (),
            list_source_items,
            (SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
        _registration(
            "readScreenplaySourceItemV1",
            "读取一个冻结原作来源项；若正文 digest 已变化则拒绝读取。",
            "读取冻结原作来源",
            {"sourceId": {"type": "string", "minLength": 1, "maxLength": 256}},
            ("sourceId",),
            read_source_item,
            (SCREENPLAY_OPERATION_SCOPE_STATE_KEY,),
        ),
    )


def _screenplay_display_params(state, arguments, name, label):
    """富展示参数：复用 legacy 细粒度标签逻辑，按写作端 toolArguments 契约
    投影（searchQuery/readTargets/targetDetail 等），前端据此渲染工具行。"""
    from agents.screenplay.legacy_tool_labels import (
        legacy_screenplay_operation_display_params,
    )

    domain: dict = {}
    for key in (SCREENPLAY_ROOT_SCOPE_STATE_KEY, SCREENPLAY_OPERATION_SCOPE_STATE_KEY):
        raw = getattr(state, "domain", {}).get(key)
        if isinstance(raw, dict):
            domain = {**domain, **raw}
    params = legacy_screenplay_operation_display_params(domain, arguments, name)
    display_names = params.get("displayNames") or {"zh-CN": label, "en": name}
    tool_arguments = {
        key: value for key, value in params.items() if key != "displayNames"
    }
    return {
        "displayNames": display_names,
        **({"toolArguments": tool_arguments} if tool_arguments else {}),
    }


def _registration(name, description, label, properties, required, handler, host_paths):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
            display_names={"zh-CN": label, "en": name},
        ),
        handler=handler,
        policy=ToolPolicy(ToolExecutionMode.READ, label, ToolRiskLevel.READ),
        concurrency_safe=True,
        data_contract=ToolDataContract(
            model_owned_paths=tuple(properties),
            host_bound_paths=host_paths,
        ),
        operation_display_params=lambda state, arguments, call, name=name, label=label: (
            _screenplay_display_params(state, arguments, name, label)
        ),
    )


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError("Screenplay replacement tool scope is unavailable")
    return value


def _text(value, name):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"Screenplay replacement {name} is required")
    return normalized


def _result(payload):
    return ToolHandlerResult(content=json.dumps(payload, ensure_ascii=False, allow_nan=False))


def _error(code):
    return ToolHandlerResult(
        content=json.dumps({"success": False, "code": code}, ensure_ascii=False),
        error_code=code,
    )


async def _record_access(
    store,
    *,
    state,
    scope,
    access_kind,
    resource_ref,
    content_digest,
    metadata,
):
    # Direct catalog tests historically use partial state. Runtime state is
    # always produced from the canonical Operation scope and must be receipted.
    if not scope.get("operationScopeId"):
        return
    operation_scope = ScreenplayPartOperationScope.from_mapping(scope)
    run_id = str(state.run_id or "").strip()
    if not run_id:
        raise ValueError("Screenplay access receipt Run identity is unavailable")
    await store.record(
        scope=operation_scope,
        run_id=run_id,
        access_kind=access_kind,
        resource_ref=resource_ref,
        content_digest=content_digest,
        metadata=metadata,
    )


def _bounded_json(value, *, full_length: int, limit: int):
    raw = str(value or "{}")
    if full_length > limit:
        return {"omitted": True, "reason": "payload_too_large"}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Screenplay stored JSON payload must be an object")
    return parsed


def _source_items(scope):
    raw = scope.get("sourceItems")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Screenplay replacement source items are unavailable")
    items = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {
            "sourceType", "sourceId", "contentDigest",
        }:
            raise ValueError("Screenplay replacement source item is invalid")
        if item["sourceType"] != "chapter":
            raise ValueError("Screenplay replacement source type is unsupported")
        items.append(item)
    if len({item["sourceId"] for item in items}) != len(items):
        raise ValueError("Screenplay replacement source items are duplicated")
    return items


async def _bound_episode(db, *, project_id, revision_id, role, episode_number):
    row = await db.fetch_one(
        "SELECT p.payload_json, length(p.payload_json) AS payload_length "
        "FROM screenplay_revision_parts AS p "
        "JOIN screenplay_revisions AS r ON r.id = p.revision_id "
        "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
        "WHERE p.revision_id = ? AND r.project_id = ? AND d.role = ? "
        "AND p.part_type = 'episode' AND p.part_key = ?",
        [revision_id, project_id, role, str(episode_number)],
    )
    if row is None:
        return None
    if int(row.get("payload_length") or 0) > 80_000:
        return None
    try:
        payload = json.loads(str(row.get("payload_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


async def _bound_revision_digest(db, *, project_id, revision_id, role):
    row = await db.fetch_one(
        "SELECT r.content_digest FROM screenplay_revisions AS r "
        "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
        "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
        [revision_id, project_id, role],
    )
    return None if row is None else str(row["content_digest"])


def _scene_draft(episode, scene_id):
    values = episode.get("sceneTexts") or episode.get("scenes") or ()
    if not isinstance(values, list):
        return None
    matches = [
        dict(item) for item in values
        if isinstance(item, dict)
        and str(item.get("sceneId") or item.get("id") or "").strip() == scene_id
    ]
    return matches[0] if len(matches) == 1 else None


__all__ = [
    "SCREENPLAY_OPERATION_SCOPE_STATE_KEY",
    "SCREENPLAY_ROOT_SCOPE_STATE_KEY",
    "build_screenplay_replacement_read_registrations",
]
