"""Bounded, scope-checked reads for screenplay tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from purra.artifacts import ArtifactStatus
from purra.json_values import thaw_json_mapping

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from domains.screenplay.source_scope import (
    is_restricted_source_scope,
    parse_source_scope,
    scoped_chapters,
    scoped_outline_ids,
    source_scope_summary,
)
from domains.screenplay_agent.tools.errors import ScreenplayToolInputError
from infrastructure.persistence.writing import SqliteStoryMemoryRecallRepository
from infrastructure.persistence.sqlite_artifact_repository import (
    SqliteArtifactRepository,
)
from utils.book_structure import load_chapter_texts
from utils.text import extract_text_from_lexical


def _json(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}\n…（已截断）"


def _page(arguments: Mapping[str, Any]) -> tuple[int, int]:
    return max(0, int(arguments.get("cursor") or 0)), max(
        1, min(50, int(arguments.get("limit") or 20))
    )


class ScreenplayToolQuery:
    def __init__(self, db) -> None:
        self._db = db
        self._screenplay = ScreenplayAgentContextQuery(db)
        self._story_memory = SqliteStoryMemoryRecallRepository(db)
        self._artifacts = SqliteArtifactRepository(db)

    async def task_dependencies(self, scope, arguments) -> dict[str, Any]:
        requested = arguments.get("partKeys")
        if (
            not isinstance(requested, list)
            or not 1 <= len(requested) <= 12
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value.strip()) > 256
                for value in requested
            )
        ):
            raise ScreenplayToolInputError(
                "partKeys must contain between 1 and 12 valid Part keys.",
                guidance=(
                    "Choose unique keys from dependencyPartKeys in the current "
                    "task input and request no more than 12 at once."
                ),
            )
        part_keys = tuple(value.strip() for value in requested)
        if len(part_keys) != len(set(part_keys)):
            raise ScreenplayToolInputError(
                "partKeys must be unique.",
                guidance="Remove duplicate Part keys and retry the read.",
            )
        allowed_value = scope.get("dependencyPartKeys")
        if (
            not isinstance(allowed_value, Sequence)
            or isinstance(allowed_value, (str, bytes))
            or any(
                not isinstance(value, str) for value in allowed_value
            )
        ):
            raise ScreenplayToolInputError(
                "The host-bound dependency scope is invalid.",
                guidance=(
                    "Do not invent dependency keys; stop this Run and retry "
                    "the task."
                ),
            )
        allowed = tuple(value.strip() for value in allowed_value)
        outside = tuple(key for key in part_keys if key not in allowed)
        if outside:
            raise ScreenplayToolInputError(
                "partKeys contains a Part outside the current Unit dependency scope.",
                guidance="Use only keys listed in dependencyPartKeys.",
                details={"invalidPartKeys": list(outside)},
            )

        task_id = str(scope.get("taskId") or "").strip()
        unit_id = str(scope.get("unitId") or "").strip()
        project_id = str(scope.get("projectId") or "").strip()
        current = await self._db.fetch_one(
            "SELECT u.dependencies_json FROM ai_agent_long_tasks AS t "
            "JOIN ai_agent_long_task_units AS u ON u.task_id = t.id "
            "WHERE t.id = ? AND t.owner_id = ? AND u.unit_id = ?",
            [task_id, project_id, unit_id],
        )
        if current is None:
            raise ScreenplayToolInputError(
                "The current task Unit does not exist in the bound project.",
                guidance="Stop this Run and retry the durable task from the host.",
            )
        direct_ids = _json_string_list(current.get("dependencies_json"))
        if any(key not in direct_ids for key in allowed):
            raise ScreenplayToolInputError(
                "The host-bound dependency scope is not a direct Unit dependency.",
                guidance="Stop this Run and retry the durable task from the host.",
            )
        rows = await self._db.fetch_all(
            "SELECT unit_id, semantic_key, status, output_ref, metadata_json "
            "FROM ai_agent_long_task_units WHERE task_id = ?",
            [task_id],
        )
        by_key = {
            str(row.get("semantic_key") or row["unit_id"]): row
            for row in rows
            if str(row["unit_id"]) in direct_ids
        }
        dependencies = []
        content_limit = min(16_000, max(2_000, 36_000 // len(part_keys)))
        for key in part_keys:
            row = by_key.get(key)
            if (
                row is None
                or str(row.get("status") or "") != "completed"
                or not str(row.get("output_ref") or "").strip()
            ):
                raise ScreenplayToolInputError(
                    "The requested Part is not a completed direct dependency.",
                    guidance=(
                        "Use only completed keys listed in dependencyPartKeys; "
                        "otherwise let the host scheduler finish the dependency first."
                    ),
                    details={"invalidPartKeys": [key]},
                )
            output = await self._part_output(
                str(row["output_ref"]),
                project_id=project_id,
                task_id=task_id,
                unit_id=str(row["unit_id"]),
            )
            dependencies.append(_task_dependency_payload(
                row,
                output,
                content_limit=content_limit,
            ))
        return {"dependencies": dependencies}

    async def _part_output(
        self,
        output_ref: str,
        *,
        project_id: str,
        task_id: str,
        unit_id: str,
    ) -> dict[str, Any]:
        prefix = "screenplay-part-artifact://"
        if not output_ref.startswith(prefix):
            raise RuntimeError("screenplay dependency output is not a Part Artifact")
        artifact = await self._artifacts.load(output_ref.removeprefix(prefix))
        if artifact is None or artifact.status is not ArtifactStatus.FINALIZED:
            raise RuntimeError("screenplay dependency Part Artifact is not finalized")
        if (
            artifact.namespace != "purrtypos.screenplay"
            or artifact.kind != "manifest_part"
            or artifact.owner_id != project_id
            or artifact.owner_ref.kind != "long_task_unit"
            or artifact.owner_ref.id != f"{task_id}:{unit_id}"
        ):
            raise RuntimeError("screenplay dependency Part Artifact scope conflicts")
        batches = tuple(await self._artifacts.list_batches(artifact.id))
        if len(batches) != 1 or len(batches[0].items) != 1:
            raise RuntimeError("screenplay dependency Part Artifact is incomplete")
        item = thaw_json_mapping(batches[0].items[0])
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            raise RuntimeError("screenplay dependency Part payload is invalid")
        return {
            **dict(payload),
            "runId": str(
                payload.get("runId") or artifact.created_by_run_id or ""
            ),
        }

    async def inspect_project(self, scope, _arguments) -> dict[str, Any]:
        project = await self._project(scope)
        heads = await self._screenplay.heads(
            str(project["id"]),
            text_limit=0,
        )
        return {
            "project": {
                "id": str(project["id"]),
                "title": str(project.get("title") or ""),
                "format": str(project.get("format") or ""),
                "stage": str(project.get("active_stage") or ""),
                "sourceKind": str(project.get("source_kind") or "original"),
                "sourceScope": _tool_scope_summary(
                    project.get("source_scope_json")
                ),
            },
            "acceptedDeliverables": [{
                "role": item["role"],
                "revisionId": item["revisionId"],
            } for item in heads],
        }

    async def read_deliverable(self, scope, arguments) -> dict[str, Any]:
        project_id = str(scope["projectId"])
        role = str(arguments["role"])
        revision_id = str(arguments.get("revisionId") or "").strip()
        episode = arguments.get("episodeNumber")
        if "boundEpisodeNumber" in scope:
            bound_episode = scope.get("boundEpisodeNumber")
            if (
                isinstance(bound_episode, bool)
                or not isinstance(bound_episode, int)
                or bound_episode < 1
            ):
                raise ScreenplayToolInputError(
                    "The host-bound episode scope is invalid.",
                    guidance="Stop this Run and retry the task from the host.",
                )
            if episode is not None and int(episode) != bound_episode:
                raise ScreenplayToolInputError(
                    "episodeNumber is outside this Run's evidence scope.",
                    guidance="Use the episodeNumber bound to the current Part.",
                    details={"invalidEpisodeNumber": int(episode)},
                )
            episode = bound_episode
        if "deliverableRevisionScope" in scope:
            revision_scope = scope.get("deliverableRevisionScope")
            if not isinstance(revision_scope, Mapping):
                raise ScreenplayToolInputError(
                    "The host-bound deliverable Revision scope is invalid.",
                    guidance="Stop this Run and retry the task from the host.",
                )
            authorized_revision_id = str(
                revision_scope.get(role) or ""
            ).strip()
            if not authorized_revision_id:
                raise ScreenplayToolInputError(
                    "The requested deliverable role is outside this Run's evidence scope.",
                    guidance=(
                        "Use only a role and revisionId present in the current "
                        "evidenceDescriptor.acceptedRevisionIds."
                    ),
                    details={"invalidRole": role},
                )
            if revision_id and revision_id != authorized_revision_id:
                raise ScreenplayToolInputError(
                    "revisionId is outside this Run's evidence scope.",
                    guidance=(
                        "Use the exact revisionId bound to this role in the "
                        "current evidenceDescriptor."
                    ),
                    details={
                        "invalidRevisionId": revision_id,
                        "role": role,
                    },
                )
            revision_id = authorized_revision_id
        if revision_id:
            row = await self._db.fetch_one(
                "SELECT r.id FROM screenplay_revisions AS r "
                "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
                "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
                [revision_id, project_id, role],
            )
            if row is None:
                raise ScreenplayToolInputError(
                    "revisionId does not identify the requested role in this project.",
                    guidance=(
                        "Use a revisionId returned for the same role by "
                        "inspectScreenplayProject or searchScreenplayDeliverables."
                    ),
                    details={"invalidRevisionId": revision_id, "role": role},
                )
        else:
            row = await self._db.fetch_one(
                "SELECT h.revision_id AS id FROM screenplay_project_heads AS h "
                "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
                "WHERE h.project_id = ? AND d.role = ?",
                [project_id, role],
            )
            if row is None:
                return {"role": role, "available": False}
            revision_id = str(row["id"])
        if episode is not None and role == "structure":
            part = await self._db.fetch_one(
                "SELECT payload_json, content_text FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'document' "
                "AND part_key = 'main'",
                [revision_id],
            )
            payload = _json((part or {}).get("payload_json"))
            selected = next((
                dict(item)
                for item in payload.get("episodes") or ()
                if isinstance(item, Mapping)
                and int(item.get("number") or 0) == int(episode)
            ), None)
            part = (
                {
                    "payload_json": json.dumps(
                        {"episode": selected},
                        ensure_ascii=False,
                    ),
                    "content_text": "",
                }
                if selected is not None
                else None
            )
        elif episode is not None:
            part = await self._db.fetch_one(
                "SELECT payload_json, content_text FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'episode' AND part_key = ?",
                [revision_id, str(int(episode))],
            )
        else:
            part = await self._db.fetch_one(
                "SELECT payload_json, content_text FROM screenplay_revision_parts "
                "WHERE revision_id = ? AND part_type = 'document' "
                "AND part_key = 'main'",
                [revision_id],
            )
        return {
            "role": role,
            "revisionId": revision_id,
            "available": part is not None,
            **(
                {
                    "payload": _bounded_payload(
                        _json(part.get("payload_json")),
                        32_000,
                    ),
                    "contentText": _clip(part.get("content_text"), 32_000),
                }
                if part is not None
                else {}
            ),
        }

    async def search_deliverables(self, scope, arguments) -> dict[str, Any]:
        query = str(arguments["query"]).strip()
        roles = tuple(str(item) for item in arguments.get("roles") or ())
        limit = max(1, min(50, int(arguments.get("limit") or 12)))
        params: list[object] = [str(scope["projectId"]), f"%{query}%"]
        role_sql = ""
        if roles:
            role_sql = " AND d.role IN (" + ",".join("?" for _ in roles) + ")"
            params.extend(roles)
        params.append(limit)
        rows = await self._db.fetch_all(
            "SELECT h.revision_id, d.role, p.part_type, p.part_key, "
            "p.content_text FROM screenplay_revisions AS r "
            "JOIN screenplay_project_heads AS h ON h.revision_id = r.id "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revision_parts AS p ON p.revision_id = h.revision_id "
            "WHERE h.project_id = ? AND p.content_text LIKE ?"
            f"{role_sql} ORDER BY d.role, p.position LIMIT ?",
            params,
        )
        return {"matches": [{
            "revisionId": str(row["revision_id"]),
            "role": str(row["role"]),
            "partType": str(row["part_type"]),
            "partKey": str(row["part_key"]),
            "excerpt": _excerpt(str(row.get("content_text") or ""), query),
        } for row in rows]}

    async def episode_context(self, scope, arguments) -> dict[str, Any]:
        project_id = str(scope["projectId"])
        episode_number = int(arguments["episodeNumber"])
        if "boundEpisodeNumber" in scope:
            bound_episode = scope.get("boundEpisodeNumber")
            if (
                isinstance(bound_episode, bool)
                or not isinstance(bound_episode, int)
                or bound_episode < 1
            ):
                raise ScreenplayToolInputError(
                    "The host-bound episode scope is invalid.",
                    guidance="Stop this Run and retry the task from the host.",
                )
            if episode_number != bound_episode:
                raise ScreenplayToolInputError(
                    "episodeNumber is outside this Run's evidence scope.",
                    guidance="Use the episodeNumber bound to the current Part.",
                    details={"invalidEpisodeNumber": episode_number},
                )
        revision_id = str(arguments.get("draftRevisionId") or "").strip() or None
        scene_list_revision_id = str(
            arguments.get("sceneListRevisionId") or ""
        ).strip() or None
        if "deliverableRevisionScope" in scope:
            revision_scope = scope.get("deliverableRevisionScope")
            if not isinstance(revision_scope, Mapping):
                raise ScreenplayToolInputError(
                    "The host-bound deliverable Revision scope is invalid.",
                    guidance="Stop this Run and retry the task from the host.",
                )
            bound_scene_list = str(
                revision_scope.get("sceneList") or ""
            ).strip()
            bound_draft = str(
                revision_scope.get("screenplayDraft") or ""
            ).strip()
            if not bound_scene_list:
                raise ScreenplayToolInputError(
                    "The current Run has no bound sceneList Revision.",
                    guidance="Stop this Run and retry the task from the host.",
                )
            if (
                scene_list_revision_id
                and scene_list_revision_id != bound_scene_list
            ):
                raise ScreenplayToolInputError(
                    "sceneListRevisionId is outside this Run's evidence scope.",
                    guidance=(
                        "Use the exact sceneListRevisionId from evidenceDescriptor."
                    ),
                    details={"invalidSceneListRevisionId": scene_list_revision_id},
                )
            if revision_id and revision_id != bound_draft:
                raise ScreenplayToolInputError(
                    "draftRevisionId is outside this Run's evidence scope.",
                    guidance="Use the exact draftRevisionId from evidenceDescriptor.",
                    details={"invalidDraftRevisionId": revision_id},
                )
            scene_list_revision_id = bound_scene_list
            revision_id = bound_draft or None
        if revision_id and not await self._revision_matches_role(
            project_id, revision_id, "screenplayDraft"
        ):
            raise ScreenplayToolInputError(
                "draftRevisionId is not a screenplayDraft revision in this project.",
                guidance=(
                    "Use the screenplayDraft revisionId returned by "
                    "inspectScreenplayProject."
                ),
                details={"invalidDraftRevisionId": revision_id},
            )
        if scene_list_revision_id and not await self._revision_matches_role(
            project_id, scene_list_revision_id, "sceneList"
        ):
            raise ScreenplayToolInputError(
                "sceneListRevisionId is not a sceneList revision in this project.",
                guidance=(
                    "Use the sceneList revisionId from the task evidence descriptor "
                    "or inspectScreenplayProject."
                ),
                details={"invalidSceneListRevisionId": scene_list_revision_id},
            )
        available_scene_numbers = (
            await self._screenplay.revision_episode_numbers(
                scene_list_revision_id
            )
            if scene_list_revision_id
            else (
                await self._screenplay.available_episode_numbers(
                    project_id,
                    draft_revision_id=revision_id,
                )
            )["sceneList"]
        )
        if episode_number not in available_scene_numbers:
            raise ScreenplayToolInputError(
                "episodeNumber does not exist in the selected scene list.",
                guidance=(
                    "Use an episode number present in the accepted sceneList "
                    "deliverable."
                ),
                details={
                    "invalidEpisodeNumber": episode_number,
                    "availableEpisodeNumbers": list(available_scene_numbers),
                },
            )
        context = await self._screenplay.episode_context(
            project_id,
            episode_number,
            draft_revision_id=revision_id,
            scene_list_revision_id=scene_list_revision_id,
        )
        return {
            **context,
            "previousEpisode": _continuity_only(context.get("previousEpisode")),
            "currentDraft": _bounded_payload(
                context.get("currentDraft"),
                60_000,
            ),
        }

    async def inspect_source_structure(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        chapters = await scoped_chapters(self._db, project)
        cursor, limit = _page(arguments)
        page = chapters[cursor:cursor + limit]
        return {
            "scope": _tool_scope_summary(project.get("source_scope_json")),
            "chapters": [{
                "chapterId": str(item["id"]),
                "title": str(item.get("title") or ""),
                "index": int(item.get("index") or 0),
                "volumeId": str(item.get("volume_id") or "") or None,
                "volumeTitle": str(item.get("volume_title") or "") or None,
            } for item in page],
            "nextCursor": cursor + len(page),
            "hasMore": cursor + len(page) < len(chapters),
        }

    async def read_source_chapters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        allowed = {
            str(item["id"]): item
            for item in await scoped_chapters(self._db, project)
        }
        requested = tuple(str(item) for item in arguments["chapterIds"])
        outside = set(requested) - set(allowed)
        if outside:
            mappings = await self._outline_chapter_mappings(
                project,
                outside,
                allowed_chapter_ids=set(allowed),
            )
            raise ScreenplayToolInputError(
                "chapterIds contains identifiers outside the readable adaptation scope.",
                guidance=(
                    "Use chapterId from inspectSourceStructure or the chapterId "
                    "field returned by readSourceOutline; do not pass outlineId."
                ),
                details={
                    "invalidChapterIds": sorted(outside),
                    **({"outlineIdMappings": mappings} if mappings else {}),
                },
            )
        texts = await load_chapter_texts(self._db, requested)
        text_limit = max(2_000, 48_000 // len(requested))
        return {"chapters": [{
            "chapterId": chapter_id,
            "title": str(allowed[chapter_id].get("title") or ""),
            "index": int(allowed[chapter_id].get("index") or 0),
            "content": _clip(texts.get(chapter_id, ""), text_limit),
        } for chapter_id in requested]}

    async def search_source_text(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        query = str(arguments["query"]).strip()
        limit = max(1, min(50, int(arguments.get("limit") or 12)))
        chapters = await scoped_chapters(self._db, project)
        allowed = {str(item["id"]): item for item in chapters}
        matches: list[dict[str, Any]] = []
        chapter_ids = list(allowed)
        for offset in range(0, len(chapter_ids), 400):
            batch = chapter_ids[offset:offset + 400]
            marks = ",".join("?" for _ in batch)
            rows = await self._db.fetch_all(
                "SELECT chapter_id, content FROM articles "
                f"WHERE chapter_id IN ({marks}) AND content LIKE ?",
                [*batch, f"%{query}%"],
            )
            rows.sort(key=lambda row: int(
                allowed[str(row["chapter_id"])].get("index") or 0
            ))
            for row in rows:
                raw = str(row.get("content") or "")
                text = extract_text_from_lexical(raw) if raw else ""
                matches.append({
                    "chapterId": str(row["chapter_id"]),
                    "chapterTitle": str(
                        allowed[str(row["chapter_id"])].get("title") or ""
                    ),
                    "excerpt": _excerpt(text, query),
                })
            if len(matches) >= limit:
                break
        return {"matches": matches[:limit]}

    async def list_characters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "人物资料")
        cursor, limit = _page(arguments)
        query = str(arguments.get("query") or "").strip()
        params: list[object] = [str(project["source_book_id"])]
        query_sql = ""
        if query:
            query_sql = " AND (name LIKE ? OR tags LIKE ?)"
            params.extend((f"%{query}%", f"%{query}%"))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, name, tags FROM characters WHERE book_id = ?"
            f"{query_sql} ORDER BY id LIMIT ? OFFSET ?",
            params,
        )
        return _catalog_page(
            rows,
            cursor,
            limit,
            "characters",
            id_key="characterId",
        )

    async def read_characters(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "人物资料")
        ids = tuple(int(item) for item in arguments["characterIds"])
        rows = await self._rows_by_ids(
            "characters", str(project["source_book_id"]), ids
        )
        profile_limit = max(1_500, 40_000 // len(ids))
        return {"characters": [{
            "characterId": int(row["id"]),
            "name": str(row.get("name") or ""),
            "tags": str(row.get("tags") or ""),
            "profile": _clip(row.get("profile_md"), profile_limit),
        } for row in rows]}

    async def list_world_entities(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "世界设定")
        cursor, limit = _page(arguments)
        types = tuple(str(item) for item in arguments.get("types") or ())
        query = str(arguments.get("query") or "").strip()
        clauses = ["book_id = ?"]
        params: list[object] = [str(project["source_book_id"])]
        if types:
            clauses.append("entity_type IN (" + ",".join("?" for _ in types) + ")")
            params.extend(types)
        if query:
            clauses.append("(name LIKE ? OR tags LIKE ?)")
            params.extend((f"%{query}%", f"%{query}%"))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, entity_type, name, tags FROM setting_entities WHERE "
            + " AND ".join(clauses)
            + " ORDER BY entity_type, id LIMIT ? OFFSET ?",
            params,
        )
        return _catalog_page(
            rows,
            cursor,
            limit,
            "entities",
            id_key="entityId",
        )

    async def read_world_entities(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "世界设定")
        ids = tuple(int(item) for item in arguments["entityIds"])
        rows = await self._rows_by_ids(
            "setting_entities", str(project["source_book_id"]), ids
        )
        profile_limit = max(1_500, 40_000 // len(ids))
        return {"entities": [{
            "entityId": int(row["id"]),
            "type": str(row.get("entity_type") or "other"),
            "name": str(row.get("name") or ""),
            "tags": str(row.get("tags") or ""),
            "profile": _clip(row.get("profile_md"), profile_limit),
        } for row in rows]}

    async def read_background(self, scope, _arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        self._require_unscoped_catalog(project, "故事背景")
        row = await self._db.fetch_one(
            "SELECT content FROM story_background WHERE book_id = ?",
            [str(project["source_book_id"])],
        )
        return {"content": _clip((row or {}).get("content"), 24_000)}

    async def query_story_facts(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        chapter_ids = tuple(
            str(item["id"]) for item in await scoped_chapters(self._db, project)
        )
        items = await self._story_memory.search_current(
            str(project["source_book_id"]),
            str(arguments["query"]),
            kinds=tuple(str(item) for item in arguments.get("kinds") or ()),
            chapter_ids=chapter_ids,
            limit=max(1, min(50, int(arguments.get("limit") or 16))),
        )
        return {"facts": [{
            "factId": item.record_id,
            "kind": item.kind,
            "key": item.memory_key,
            "payload": _bounded_payload(item.payload, 3_000),
            "evidence": {
                "chapterId": item.chapter_id,
                "excerpt": _clip(item.source_excerpt, 1_200),
            },
        } for item in items]}

    async def read_outline(self, scope, arguments) -> dict[str, Any]:
        project = await self._source_project(scope)
        requested = tuple(str(item) for item in arguments.get("outlineIds") or ())
        allowed = await scoped_outline_ids(self._db, project)
        params: list[object] = [str(project["source_book_id"])]
        clauses = ["book_id = ?", "type != 'writing'"]
        if is_restricted_source_scope(project):
            if not allowed:
                return {
                    "outlines": [],
                    "nextCursor": int(arguments.get("cursor") or 0),
                    "hasMore": False,
                }
            if requested and set(requested) - allowed:
                outside = set(requested) - allowed
                raise ScreenplayToolInputError(
                    "outlineIds contains identifiers outside the adaptation scope.",
                    guidance=(
                        "Call readSourceOutline without outlineIds to page through "
                        "the readable outline catalog, then reuse outlineId values."
                    ),
                    details={"invalidOutlineIds": sorted(outside)},
                )
            selected = requested or tuple(sorted(allowed))
            clauses.append("id IN (" + ",".join("?" for _ in selected) + ")")
            params.extend(selected)
        elif requested:
            clauses.append("id IN (" + ",".join("?" for _ in requested) + ")")
            params.extend(requested)
        cursor, limit = _page(arguments)
        if requested:
            cursor = 0
            limit = max(limit, len(requested))
        params.extend((limit + 1, cursor))
        rows = await self._db.fetch_all(
            "SELECT id, title, type, writing_chapter_id, markdown_content "
            "FROM outlines WHERE "
            + " AND ".join(clauses)
            + " ORDER BY sort, id LIMIT ? OFFSET ?",
            params,
        )
        page = rows[:limit]
        if requested:
            found = {str(row["id"]) for row in rows}
            missing = set(requested) - found
            if missing:
                raise ScreenplayToolInputError(
                    "outlineIds contains identifiers outside the source book.",
                    guidance=(
                        "Use outlineId values returned by readSourceOutline for "
                        "this project."
                    ),
                    details={"invalidOutlineIds": sorted(missing)},
                )
        content_limit = max(2_000, 48_000 // max(1, len(page)))
        return {
            "outlines": [{
            "outlineId": str(row["id"]),
            **(
                {"chapterId": str(row["writing_chapter_id"])}
                if str(row.get("writing_chapter_id") or "").strip()
                else {}
            ),
            "title": str(row.get("title") or ""),
            "type": str(row.get("type") or ""),
            "content": _clip(row.get("markdown_content"), content_limit),
            } for row in page],
            "nextCursor": cursor + len(page),
            "hasMore": len(rows) > limit,
        }

    @staticmethod
    def source_refs(
        tool_name: str,
        result: Mapping[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        coverage = {
            "inspectSourceStructure": "catalog",
            "readSourceChapters": "full",
            "searchSourceText": "search",
            "listSourceCharacters": "catalog",
            "readSourceCharacters": "referenced",
            "listSourceWorldEntities": "catalog",
            "readSourceWorldEntities": "referenced",
            "readSourceBackground": "full",
            "querySourceStoryFacts": "search",
            "readSourceOutline": "referenced",
        }.get(tool_name)
        if coverage is None:
            return ()
        key, source_type, id_key, excerpt_key = {
            "inspectSourceStructure": (
                "chapters", "chapter", "chapterId", "title"
            ),
            "readSourceChapters": (
                "chapters", "chapter", "chapterId", "content"
            ),
            "searchSourceText": (
                "matches", "chapter", "chapterId", "excerpt"
            ),
            "listSourceCharacters": (
                "characters", "character", "characterId", "name"
            ),
            "readSourceCharacters": (
                "characters", "character", "characterId", "profile"
            ),
            "listSourceWorldEntities": (
                "entities", "world_entity", "entityId", "name"
            ),
            "readSourceWorldEntities": (
                "entities", "world_entity", "entityId", "profile"
            ),
            "querySourceStoryFacts": (
                "facts", "story_fact", "factId", "evidence"
            ),
            "readSourceOutline": (
                "outlines", "outline", "outlineId", "content"
            ),
        }.get(tool_name, ("", "", "", ""))
        if tool_name == "readSourceBackground":
            value = result.get("content")
            return ({
                "sourceType": "story_background",
                "sourceId": "main",
                "sourceRevision": _digest(value),
                "coverageMode": coverage,
                "excerpt": _clip(value, 1_000),
            },)
        items = result.get(key)
        if not isinstance(items, list):
            return ()
        refs = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            source_id = str(item.get(id_key) or "").strip()
            if not source_id:
                continue
            excerpt = item.get(excerpt_key)
            if isinstance(excerpt, Mapping):
                excerpt = excerpt.get("excerpt")
            refs.append({
                "sourceType": source_type,
                "sourceId": source_id,
                "sourceRevision": _digest(item),
                "coverageMode": coverage,
                "excerpt": _clip(excerpt, 1_000),
            })
        return tuple(refs)

    async def _project(self, scope) -> dict[str, Any]:
        row = await self._db.fetch_one(
            "SELECT * FROM screenplay_projects WHERE id = ?",
            [str(scope["projectId"])],
        )
        if row is None:
            raise LookupError("screenplay project does not exist")
        return dict(row)

    async def _source_project(self, scope) -> dict[str, Any]:
        project = await self._project(scope)
        book_id = str(project.get("source_book_id") or "").strip()
        if not book_id or book_id != str(scope.get("sourceBookId") or ""):
            raise ValueError("screenplay project has no bound source book")
        run_scope = scope.get("sourceScope")
        if isinstance(run_scope, Mapping) and is_restricted_source_scope(run_scope):
            persisted_ids = {
                str(item["id"])
                for item in await scoped_chapters(self._db, project)
            }
            narrowed = parse_source_scope(run_scope)
            narrowed_ids = set(narrowed["chapterIds"])
            if not narrowed_ids or not narrowed_ids.issubset(persisted_ids):
                raise ValueError("screenplay source Run scope widens project scope")
            project["source_scope_json"] = json.dumps(
                narrowed,
                ensure_ascii=False,
            )
        return project

    @staticmethod
    def _require_unscoped_catalog(project, label: str) -> None:
        if is_restricted_source_scope(project):
            raise ScreenplayToolInputError(
                f"{label} cannot be read directly for a chapter-restricted adaptation.",
                guidance=(
                    "Use querySourceStoryFacts to retrieve chapter-grounded "
                    "information within the licensed adaptation scope."
                ),
            )

    async def _revision_matches_role(
        self,
        project_id: str,
        revision_id: str,
        role: str,
    ) -> bool:
        return await self._db.fetch_one(
            "SELECT 1 FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "WHERE r.id = ? AND r.project_id = ? AND d.role = ?",
            [revision_id, project_id, role],
        ) is not None

    async def _outline_chapter_mappings(
        self,
        project: Mapping[str, Any],
        identifiers: set[str],
        *,
        allowed_chapter_ids: set[str],
    ) -> list[dict[str, str]]:
        if not identifiers:
            return []
        marks = ",".join("?" for _ in identifiers)
        rows = await self._db.fetch_all(
            "SELECT id, writing_chapter_id FROM outlines WHERE book_id = ? "
            f"AND id IN ({marks}) AND writing_chapter_id IS NOT NULL",
            [str(project["source_book_id"]), *sorted(identifiers)],
        )
        return [{
            "outlineId": str(row["id"]),
            "chapterId": str(row["writing_chapter_id"]),
        } for row in rows if str(row["writing_chapter_id"]) in allowed_chapter_ids]

    async def _rows_by_ids(
        self,
        table: str,
        book_id: str,
        ids: Sequence[int],
    ) -> list[dict[str, Any]]:
        marks = ",".join("?" for _ in ids)
        rows = await self._db.fetch_all(
            f"SELECT * FROM {table} WHERE book_id = ? AND id IN ({marks})",
            [book_id, *ids],
        )
        by_id = {int(row["id"]): row for row in rows}
        if set(ids) - set(by_id):
            raise ScreenplayToolInputError(
                "One or more requested entity identifiers are outside the source book.",
                guidance=(
                    "Use identifiers returned by the matching list tool for this "
                    "project; characterId and entityId are different namespaces."
                ),
                details={"invalidIds": sorted(set(ids) - set(by_id))},
            )
        return [by_id[item] for item in ids]


def _catalog_page(
    rows,
    cursor: int,
    limit: int,
    key: str,
    *,
    id_key: str,
) -> dict[str, Any]:
    page = rows[:limit]
    return {
        key: [{
            id_key: int(row["id"]),
            **{name: value for name, value in dict(row).items() if name != "id"},
        } for row in page],
        "nextCursor": cursor + len(page),
        "hasMore": len(rows) > limit,
    }


def _tool_scope_summary(scope_value: object) -> dict[str, Any]:
    summary = source_scope_summary(scope_value)
    for key in ("firstChapter", "lastChapter"):
        chapter = summary.get(key)
        if isinstance(chapter, Mapping) and chapter.get("id") is not None:
            summary[key] = {
                "chapterId": str(chapter["id"]),
                **{name: value for name, value in chapter.items() if name != "id"},
            }
    return summary


def _json_string_list(value: object) -> tuple[str, ...]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(dict.fromkeys(
        text for item in parsed if (text := str(item).strip())
    ))


def _excerpt(text: str, query: str, radius: int = 500) -> str:
    index = text.lower().find(query.lower())
    if index < 0:
        return _clip(text, radius * 2)
    start = max(0, index - radius)
    end = min(len(text), index + len(query) + radius)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_payload(value: object, limit: int) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    payload = dict(value)
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    if len(encoded) <= limit:
        return payload
    return {
        "schemaVersion": payload.get("schemaVersion"),
        "documentKind": payload.get("documentKind"),
        "truncated": True,
        "availableKeys": sorted(str(key) for key in payload),
        "episodeCount": (
            len(payload.get("episodes"))
            if isinstance(payload.get("episodes"), list)
            else None
        ),
        "sceneCount": (
            len(payload.get("scenes"))
            if isinstance(payload.get("scenes"), list)
            else None
        ),
        "guidance": (
            "Use episodeNumber or searchScreenplayDeliverables to read a "
            "smaller relevant part."
        ),
    }


def _task_dependency_payload(
    row: Mapping[str, Any],
    output: Mapping[str, Any],
    *,
    content_limit: int,
) -> dict[str, Any]:
    metadata = _json(row.get("metadata_json"))
    content = output.get("contentJson")
    episode = output.get("episodeDraft")
    if isinstance(content, Mapping):
        content_json = _bounded_payload(content, content_limit) or {}
    elif isinstance(episode, Mapping):
        content_json = {
            key: episode[key]
            for key in (
                "episodeNumber",
                "title",
                "sceneIds",
                "continuitySummary",
            )
            if key in episode
        }
    else:
        excluded = {
            "artifactId",
            "artifactDigest",
            "candidateArtifactId",
            "contentText",
            "episodeDraft",
            "evidenceDescriptor",
            "evidenceReceipt",
            "finalResponse",
            "runId",
            "sceneText",
            "sourceRunIds",
            "validationReceipt",
        }
        content_json = {
            key: value
            for key, value in output.items()
            if key not in excluded
        }
        content_json = _bounded_payload(content_json, content_limit) or {}
    text = str(output.get("contentText") or output.get("sceneText") or "")
    if not text and isinstance(episode, Mapping):
        text = str(episode.get("contentText") or "")
    source_run_refs: list[str] = []
    source_values = output.get("sourceRunIds")
    for value in (
        output.get("runId"),
        *(
            source_values
            if isinstance(source_values, (list, tuple))
            else ()
        ),
    ):
        run_id = str(value or "").strip()
        if run_id and run_id not in source_run_refs:
            source_run_refs.append(run_id)
    return {
        "partKey": str(row.get("semantic_key") or row["unit_id"]),
        "kind": str(
            metadata.get("unitKind")
            or metadata.get("partKind")
            or "unknown"
        ),
        "contentJson": content_json,
        **({"contentTextTail": text[-1_200:]} if text else {}),
        "sourceRunRefs": source_run_refs,
    }


def _continuity_only(value: object) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        key: value.get(key)
        for key in ("episodeNumber", "title", "continuitySummary")
        if value.get(key) is not None
    }


__all__ = ["ScreenplayToolQuery"]
