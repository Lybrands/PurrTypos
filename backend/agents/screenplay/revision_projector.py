"""Atomic projection of settled replacement Parts into an immutable Revision."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence

from agents.screenplay.contracts import ScreenplayPartOperationScope
from purra.json_values import canonical_json_digest
from utils.text import extract_text_from_lexical


_PROPOSAL_KIND_BY_ROLE = {
    "sourceAnalysis": "source_analysis",
    "creativeBrief": "creative_brief",
    "structure": "episode_outline",
    "sceneList": "scene_list",
    "screenplayDraft": "scene_draft",
    "review": "review",
}


class ScreenplayReplacementRevisionProjector:
    def __init__(self, db) -> None:
        self._db = db

    async def project(
        self,
        *,
        scope: ScreenplayPartOperationScope,
        run_id: str,
        operation: str,
        dependencies: Sequence[Mapping[str, object]],
    ) -> dict[str, object]:
        if not dependencies:
            raise ValueError("Screenplay projection requires settled Parts")
        projected_run = str(run_id or "").strip()
        if not projected_run:
            raise ValueError("Screenplay projection Run is required")
        parts = _assemble_parts(
            scope.target_role,
            dependencies,
            reviewed_draft_id=scope.deliverable_revision_scope.get(
                "screenplayDraft"
            ),
        )
        frozen_parent_id = scope.deliverable_revision_scope.get(scope.target_role)
        if scope.target_role in {"sceneList", "screenplayDraft"} and frozen_parent_id:
            parts = await self._merge_episode_snapshot(
                target_role=scope.target_role,
                parent_revision_id=frozen_parent_id,
                current_parts=parts,
            )
        output_digest = canonical_json_digest(parts)
        access_receipts, input_digest = await self._verified_access_receipts(
            scope.task_id,
            semantic_keys=tuple(str(item["partKey"]) for item in dependencies),
        )
        if frozen_parent_id:
            input_digest = canonical_json_digest({
                "accessReceiptDigest": input_digest,
                "hostBaselineRevisionId": frozen_parent_id,
            })
        async with self._db.transaction(cancellation_linearizable=True):
            replay = await self._db.fetch_one(
                "SELECT * FROM screenplay_replacement_projection_receipts "
                "WHERE task_id = ?",
                [scope.task_id],
            )
            if replay is not None:
                if any((
                    replay["project_id"] != scope.project_id,
                    replay["target_role"] != scope.target_role,
                    replay["input_receipt_digest"] != input_digest,
                    replay["output_digest"] != output_digest,
                )):
                    raise ValueError("Screenplay projection replay conflicts")
                return {
                    "revisionId": str(replay["revision_id"]),
                    "outputDigest": output_digest,
                    "inputReceiptDigest": input_digest,
                    "projectionOperationScopeId": str(
                        replay["operation_scope_id"]
                    ),
                    "replayed": True,
                }
            project = await self._db.fetch_one(
                "SELECT id, status FROM screenplay_projects WHERE id = ?",
                [scope.project_id],
            )
            if project is None or str(project.get("status") or "active") == "archived":
                raise ValueError("Screenplay projection project is unavailable")
            deliverable = await self._db.fetch_one(
                "SELECT id FROM screenplay_deliverables "
                "WHERE project_id = ? AND role = ?",
                [scope.project_id, scope.target_role],
            )
            if deliverable is None:
                raise ValueError("Screenplay projection target role is unavailable")
            admissible_revisions, admissible_sources = (
                await self._admissible_inputs(scope.task_id)
            )
            revision_inputs, source_inputs = await self._validate_inputs(
                scope,
                access_receipts,
                admissible_revisions=admissible_revisions,
                admissible_sources=admissible_sources,
            )
            if (
                scope.target_role in {"sceneList", "screenplayDraft"}
                and frozen_parent_id
            ):
                baseline = await self._db.fetch_one(
                    "SELECT r.id FROM screenplay_revisions AS r "
                    "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                    "JOIN screenplay_project_heads AS h "
                    "ON h.project_id = r.project_id "
                    "AND h.deliverable_id = r.deliverable_id "
                    "AND h.revision_id = r.id "
                    "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
                    [frozen_parent_id, scope.project_id, scope.target_role],
                )
                if baseline is None:
                    raise ValueError("Screenplay episode baseline is stale")
                consumed = revision_inputs.setdefault(
                    scope.target_role, frozen_parent_id
                )
                if consumed != frozen_parent_id:
                    raise ValueError("Screenplay episode baseline receipt conflicts")
            required_model_revisions = dict(scope.deliverable_revision_scope)
            if scope.target_role in {"sceneList", "screenplayDraft"}:
                required_model_revisions.pop(scope.target_role, None)
            if any(
                revision_inputs.get(role) != revision_id
                for role, revision_id in required_model_revisions.items()
            ):
                raise ValueError(
                    "Screenplay projection is missing a required revision receipt"
                )
            parent_revision_id = revision_inputs.get(scope.target_role)
            if operation == "revise" and not parent_revision_id:
                raise ValueError("Screenplay revision projection requires its base receipt")
            latest = await self._db.fetch_one(
                "SELECT COALESCE(MAX(revision_no), 0) AS revision_no "
                "FROM screenplay_revisions WHERE deliverable_id = ?",
                [deliverable["id"]],
            )
            revision_no = int((latest or {}).get("revision_no") or 0) + 1
            revision_id = "sprev_" + uuid.uuid4().hex
            document = parts[0]
            try:
                proposal_kind = _PROPOSAL_KIND_BY_ROLE[scope.target_role]
            except KeyError as exc:
                raise ValueError(
                    "Screenplay projection target role is unsupported"
                ) from exc
            await self._db.execute(
                "INSERT INTO screenplay_revisions "
                "(id, project_id, deliverable_id, revision_no, parent_revision_id, "
                "schema_version, content_digest, summary_json, root_run_id, "
                "finalizing_run_id, created_by, agent_task_id) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, "
                "'screenplay_agent_task', ?)",
                [
                    revision_id,
                    scope.project_id,
                    deliverable["id"],
                    revision_no,
                    parent_revision_id,
                    document["contentDigest"],
                    _dump({
                        "role": scope.target_role,
                        "proposalKind": proposal_kind,
                        "textLength": len(str(document["contentText"])),
                        "partCount": len(parts),
                        "inputReceiptDigest": input_digest,
                    }),
                    projected_run,
                    projected_run,
                    scope.task_id,
                ],
            )
            for part in parts:
                await self._db.execute(
                    "INSERT INTO screenplay_revision_parts "
                    "(revision_id, part_type, part_key, position, payload_json, "
                    "content_text, content_digest) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        revision_id,
                        part["partType"],
                        part["partKey"],
                        part["position"],
                        _dump(part["payload"]),
                        part["contentText"],
                        part["contentDigest"],
                    ],
                )
            for role, revision_input in sorted(revision_inputs.items()):
                await self._db.execute(
                    "INSERT INTO screenplay_revision_inputs "
                    "(revision_id, input_role, input_revision_id) VALUES (?, ?, ?)",
                    [revision_id, role, revision_input],
                )
            for source in source_inputs:
                await self._db.execute(
                    "INSERT INTO screenplay_revision_source_refs "
                    "(revision_id, source_type, source_id, source_revision, excerpt) "
                    "VALUES (?, ?, ?, ?, '')",
                    [
                        revision_id,
                        source["sourceType"],
                        source["sourceId"],
                        source["contentDigest"],
                    ],
                )
            await self._db.execute(
                "INSERT INTO screenplay_replacement_projection_receipts "
                "(task_id, operation_scope_id, project_id, target_role, revision_id, "
                "input_receipt_digest, output_digest, projected_by_run_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    scope.task_id,
                    scope.operation_scope_id,
                    scope.project_id,
                    scope.target_role,
                    revision_id,
                    input_digest,
                    output_digest,
                    projected_run,
                ],
            )
            return {
                "revisionId": revision_id,
                "revisionNo": revision_no,
                "outputDigest": output_digest,
                "inputReceiptDigest": input_digest,
                "projectionOperationScopeId": scope.operation_scope_id,
                "replayed": False,
            }

    async def _merge_episode_snapshot(
        self,
        *,
        target_role: str,
        parent_revision_id: str,
        current_parts: Sequence[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        previous = await self._db.fetch_all(
            "SELECT part_key, position, payload_json, content_text "
            "FROM screenplay_revision_parts WHERE revision_id = ? "
            "AND part_type = 'episode' ORDER BY position",
            [parent_revision_id],
        )
        episodes = {
            str(row["part_key"]): {
                "partType": "episode",
                "partKey": str(row["part_key"]),
                "position": int(row.get("position") or 0),
                "payload": _object(row.get("payload_json")),
                "contentText": str(row.get("content_text") or ""),
            }
            for row in previous
        }
        episodes.update({
            str(part["partKey"]): dict(part)
            for part in current_parts
            if part.get("partType") == "episode"
        })
        if not episodes:
            raise ValueError("Screenplay episode baseline has no episode Parts")
        ordered = []
        for position, key in enumerate(sorted(episodes, key=_episode_sort_key), start=1):
            part = episodes[key]
            ordered.append(_part(
                "episode",
                key,
                position,
                part["payload"],
                part["contentText"],
            ))
        return _episode_document(target_role, ordered)

    async def _verified_access_receipts(self, task_id, *, semantic_keys):
        if not semantic_keys:
            raise ValueError("Screenplay projection dependency scope is empty")
        marks = ",".join("?" for _ in semantic_keys)
        units = await self._db.fetch_all(
            "SELECT semantic_key, validation_receipt_json "
            "FROM ai_agent_long_task_units WHERE task_id = ? "
            f"AND semantic_key IN ({marks}) AND status = 'completed'",
            [task_id, *semantic_keys],
        )
        if {str(row["semantic_key"]) for row in units} != set(semantic_keys):
            raise ValueError("Screenplay projection dependencies are not settled")
        rows = await self._db.fetch_all(
            "SELECT r.operation_scope_id, r.access_kind, r.resource_ref, "
            "r.content_digest, r.metadata_json "
            "FROM screenplay_operation_access_receipts AS r "
            "JOIN ai_agent_long_task_units AS u "
            "ON u.task_id = r.task_id AND u.unit_id = r.unit_id "
            "AND u.attempt = r.attempt "
            "WHERE r.task_id = ? AND u.status = 'completed' "
            f"AND u.semantic_key IN ({marks}) "
            "ORDER BY r.operation_scope_id, r.access_kind, r.resource_ref",
            [task_id, *semantic_keys],
        )
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in rows:
            metadata = _object(row["metadata_json"])
            receipt = {
                "operationScopeId": str(row["operation_scope_id"]),
                "accessKind": str(row["access_kind"]),
                "resourceRef": str(row["resource_ref"]),
                "contentDigest": str(row["content_digest"]),
                "metadata": metadata,
            }
            grouped.setdefault(receipt["operationScopeId"], []).append(receipt)
        for unit in units:
            validation = _object(unit["validation_receipt_json"])
            operation_scope_id = str(validation.get("operationScopeId") or "")
            receipts = grouped.get(operation_scope_id, [])
            if (
                not operation_scope_id
                or validation.get("accessReceipts") != receipts
                or validation.get("accessReceiptDigest")
                != canonical_json_digest(receipts)
            ):
                raise ValueError("Screenplay Unit access receipts conflict")
        flattened = tuple(
            receipt
            for operation_scope_id in sorted(grouped)
            for receipt in grouped[operation_scope_id]
        )
        return flattened, canonical_json_digest(flattened)

    async def _admissible_inputs(self, task_id):
        row = await self._db.fetch_one(
            "SELECT metadata_json FROM ai_agent_long_tasks WHERE id = ?",
            [task_id],
        )
        if row is None:
            raise ValueError("Screenplay projection Task is unavailable")
        task_metadata = _object(row["metadata_json"])
        recipe = task_metadata.get("recipe")
        steps = recipe.get("steps") if isinstance(recipe, Mapping) else None
        if not isinstance(steps, list):
            raise ValueError("Screenplay projection recipe is unavailable")
        revisions: dict[str, set[str]] = {}
        sources: set[tuple[str, str, str]] = set()
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            # ExecutionRecipe.to_metadata flattens step metadata into the
            # canonical step object.
            metadata = step
            for role, revision_id in dict(
                metadata.get("deliverableRevisionScope") or {}
            ).items():
                revisions.setdefault(str(role), set()).add(str(revision_id))
            source_book_id = str(metadata.get("sourceBookId") or "")
            for source in metadata.get("sourceItems") or ():
                if not isinstance(source, Mapping):
                    continue
                sources.add((
                    source_book_id,
                    str(source.get("sourceId") or ""),
                    str(source.get("contentDigest") or ""),
                ))
        return revisions, sources

    async def _validate_inputs(
        self,
        scope,
        receipts,
        *,
        admissible_revisions,
        admissible_sources,
    ):
        revision_inputs: dict[str, str] = {}
        source_inputs: dict[tuple[str, str], dict[str, str]] = {}
        for receipt in receipts:
            kind = receipt["accessKind"]
            metadata = receipt["metadata"]
            if kind == "revision":
                role = str(metadata.get("role") or "").strip()
                revision_id = str(receipt["resourceRef"]).removeprefix(
                    "screenplay-revision-v1://"
                )
                if revision_id not in admissible_revisions.get(role, set()):
                    raise ValueError("Screenplay revision receipt is outside projection scope")
                row = await self._db.fetch_one(
                    "SELECT r.content_digest, h.revision_id AS head_revision_id "
                    "FROM screenplay_revisions AS r "
                    "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                    "LEFT JOIN screenplay_project_heads AS h "
                    "ON h.project_id = r.project_id AND h.deliverable_id = r.deliverable_id "
                    "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
                    [revision_id, scope.project_id, role],
                )
                if (
                    row is None
                    or row["content_digest"] != receipt["contentDigest"]
                    or row.get("head_revision_id") != revision_id
                ):
                    raise ValueError("Screenplay revision receipt is stale")
                previous = revision_inputs.setdefault(role, revision_id)
                if previous != revision_id:
                    raise ValueError("Screenplay projection consumed conflicting revisions")
            elif kind == "source_item":
                source_type = str(metadata.get("sourceType") or "")
                source_id = str(metadata.get("sourceId") or "")
                prefix = "novel-source-chapter-v1://"
                resource_ref = str(receipt["resourceRef"])
                if source_type != "chapter" or not resource_ref.startswith(prefix):
                    raise ValueError("Screenplay source receipt is invalid")
                source_path = resource_ref.removeprefix(prefix).split("/", 1)
                if len(source_path) != 2 or source_path[1] != source_id:
                    raise ValueError("Screenplay source receipt identity conflicts")
                book_id = source_path[0]
                digest = str(receipt["contentDigest"])
                if (book_id, source_id, digest) not in admissible_sources:
                    raise ValueError("Screenplay source receipt is outside projection scope")
                row = await self._db.fetch_one(
                    "SELECT a.content FROM outline_chapters AS oc "
                    "JOIN outlines AS o ON o.id = oc.outline_id "
                    "LEFT JOIN articles AS a ON a.chapter_id = oc.id "
                    "WHERE o.book_id = ? AND oc.id = ?",
                    [book_id, source_id],
                )
                raw = str((row or {}).get("content") or "")
                text = extract_text_from_lexical(raw) if raw else ""
                current_digest = "sha256:" + hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest()
                if row is None or current_digest != digest:
                    raise ValueError("Screenplay source receipt is stale")
                value = {
                    "sourceType": source_type,
                    "sourceId": source_id,
                    "contentDigest": digest,
                }
                previous = source_inputs.setdefault((source_type, source_id), value)
                if previous != value:
                    raise ValueError("Screenplay projection consumed conflicting sources")
        return revision_inputs, list(source_inputs.values())


def _assemble_parts(target_role, dependencies, *, reviewed_draft_id=None):
    if target_role == "screenplayDraft":
        return _assemble_screenplay_draft(dependencies)
    if target_role == "sceneList":
        return _assemble_scene_list(dependencies)
    if target_role == "review":
        return _assemble_review(dependencies, reviewed_draft_id=reviewed_draft_id)
    values = []
    texts = []
    for item in dependencies:
        if item.get("partKind") in {"draft_scene", "episode_metadata"}:
            raise ValueError("Screenplay document projection received episode Parts")
        payload = item.get("payload")
        candidate = payload.get("payload") if isinstance(payload, Mapping) else None
        if not isinstance(candidate, Mapping):
            raise ValueError("Screenplay document projection Candidate is invalid")
        values.append({"partKey": item["partKey"], "payload": dict(candidate)})
        text = str(candidate.get("content") or candidate.get("contentText") or "").strip()
        if text:
            texts.append(text)
    if not values:
        raise ValueError("Screenplay document projection has no Candidate Parts")
    content = {"schemaVersion": 1, "role": target_role, "parts": values}
    return [_part("document", "main", 0, content, "\n\n".join(texts))]


def _assemble_review(dependencies, *, reviewed_draft_id):
    draft_id = str(reviewed_draft_id or "").strip()
    if not draft_id:
        raise ValueError("Screenplay review projection requires its draft revision")
    if len(dependencies) != 1:
        raise ValueError("Screenplay review projection requires one Candidate Part")
    item = dependencies[0]
    payload = item.get("payload")
    candidate = payload.get("payload") if isinstance(payload, Mapping) else None
    if item.get("partKind") != "review_dimension" or not isinstance(
        candidate, Mapping
    ):
        raise ValueError("Screenplay review projection Candidate is invalid")
    issues = [
        {
            "id": str(issue["id"]).strip(),
            "severity": str(issue.get("severity") or "minor").strip(),
            "description": str(issue["description"]).strip(),
            "sceneIds": [str(scene_id).strip() for scene_id in issue["sceneIds"]],
        }
        for issue in candidate.get("issues", [])
    ]
    content = {
        "inputContractVersion": 2,
        "reviewedDraftId": draft_id,
        "verdict": candidate.get("verdict"),
        "issues": issues,
    }
    return [_part("document", "main", 0, content, "")]


def _assemble_scene_list(dependencies):
    episode_parts = []
    occupied_scenes: set[str] = set()
    for item in dependencies:
        if item.get("partKind") != "document_section":
            raise ValueError("Screenplay scene-list projection received invalid Part")
        number = item.get("episodeNumber")
        payload = item.get("payload")
        candidate = payload.get("payload") if isinstance(payload, Mapping) else None
        if type(number) is not int or number < 1 or not isinstance(candidate, Mapping):
            raise ValueError("Screenplay scene-list episode Candidate is invalid")
        if candidate.get("episodeNumber") != number:
            raise ValueError("Screenplay scene-list episode identity conflicts")
        scenes = candidate.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("Screenplay scene-list episode has no scenes")
        normalized_scenes = []
        for raw_scene in scenes:
            if not isinstance(raw_scene, Mapping):
                raise ValueError("Screenplay scene-list scene is invalid")
            scene = dict(raw_scene)
            scene_id = str(scene.get("id") or "").strip()
            if not scene_id or scene_id in occupied_scenes:
                raise ValueError("Screenplay scene-list scene identity conflicts")
            occupied_scenes.add(scene_id)
            scene["id"] = scene_id
            scene["episodeNumber"] = number
            normalized_scenes.append(scene)
        episode_payload = {
            "episodeNumber": number,
            "title": str(candidate.get("title") or "").strip(),
            "scenes": normalized_scenes,
        }
        content_text = "\n".join(
            str(scene.get("heading") or scene.get("synopsis") or scene["id"])
            for scene in normalized_scenes
        )
        episode_parts.append(_part(
            "episode", str(number), number, episode_payload, content_text
        ))
    if not episode_parts:
        raise ValueError("Screenplay scene-list projection has no episodes")
    return _episode_document("sceneList", episode_parts)


def _assemble_screenplay_draft(dependencies):
    episodes: dict[int, dict[str, object]] = {}
    for item in dependencies:
        kind = item.get("partKind")
        number = item.get("episodeNumber")
        payload = item.get("payload")
        if kind not in {"draft_scene", "episode_metadata"}:
            continue
        if type(number) is not int or number < 1 or not isinstance(payload, Mapping):
            raise ValueError("Screenplay episode projection scope is invalid")
        episode = episodes.setdefault(number, {"scenes": []})
        if kind == "draft_scene":
            episode["scenes"].append({
                "sceneId": str(payload.get("sceneId") or ""),
                "contentText": str(payload.get("sceneText") or ""),
            })
        else:
            episode["title"] = str(payload.get("title") or "")
            episode["continuitySummary"] = str(
                payload.get("continuitySummary") or ""
            )
    if not episodes or any(
        not episode.get("title") or not episode.get("scenes")
        for episode in episodes.values()
    ):
        raise ValueError("Screenplay draft projection is incomplete")
    episode_parts = []
    episode_payloads = []
    for position, (number, episode) in enumerate(sorted(episodes.items()), start=1):
        scenes = sorted(episode["scenes"], key=lambda item: item["sceneId"])
        content_text = "\n\n".join(item["contentText"] for item in scenes)
        payload = {
            "episodeNumber": number,
            "title": episode["title"],
            "continuitySummary": episode.get("continuitySummary", ""),
            "sceneIds": [item["sceneId"] for item in scenes],
            "sceneTexts": scenes,
            "contentText": content_text,
        }
        episode_payloads.append(payload)
        episode_parts.append(_part("episode", str(number), position, payload, content_text))
    return _episode_document("screenplayDraft", episode_parts)


def _episode_document(target_role, episode_parts):
    ordered = sorted(episode_parts, key=lambda part: _episode_sort_key(part["partKey"]))
    payloads = [dict(part["payload"]) for part in ordered]
    if target_role == "sceneList":
        document_payload = {
            "schemaVersion": 1,
            "role": target_role,
            "episodes": payloads,
            "scenes": [
                scene
                for episode in payloads
                for scene in episode.get("scenes", [])
            ],
        }
    elif target_role == "screenplayDraft":
        document_payload = {
            "schemaVersion": 1,
            "role": target_role,
            "episodeDrafts": payloads,
            "completedSceneIds": [
                scene_id
                for episode in payloads
                for scene_id in episode.get("sceneIds", [])
            ],
            "isComplete": True,
        }
    else:
        raise ValueError("Screenplay episode document role is invalid")
    document_text = "\n\n".join(
        str(part["contentText"]) for part in ordered if str(part["contentText"]).strip()
    )
    return [_part("document", "main", 0, document_payload, document_text), *ordered]


def _episode_sort_key(value):
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, str(value)


def _part(part_type, part_key, position, payload, content_text):
    normalized = dict(payload)
    text = str(content_text or "")
    digest = hashlib.sha256(_dump({
        "payload": normalized,
        "contentText": text,
    }).encode("utf-8")).hexdigest()
    return {
        "partType": part_type,
        "partKey": part_key,
        "position": position,
        "payload": normalized,
        "contentText": text,
        "contentDigest": digest,
    }


def _dump(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value):
    if isinstance(value, Mapping):
        return dict(value)
    parsed = json.loads(str(value or "{}"))
    if not isinstance(parsed, dict):
        raise ValueError("Screenplay projection JSON object is invalid")
    return parsed


__all__ = ["ScreenplayReplacementRevisionProjector"]
