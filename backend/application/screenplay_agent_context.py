"""Read-only, bounded context for the rewritten screenplay Agent."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.source_scope import scoped_chapters
from exceptions import AppError, NotFoundError


def _object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip(value: str, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else f"{text[:limit]}\n…（已截断）"


class ScreenplayAgentContextQuery:
    """Keep persistence details out of the Planner, Resolver and Executor."""

    def __init__(self, db) -> None:
        self._db = db

    async def planning_context(
        self,
        workspace: Mapping[str, Any],
    ) -> dict[str, Any]:
        project_id = str(workspace.get("project", {}).get("id") or "")
        project = dict(workspace.get("project") or {})
        workflow = dict(workspace.get("workflow") or {})
        heads = await self._db.fetch_all(
            "SELECT d.role, r.summary_json "
            "FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revisions AS r ON r.id = h.revision_id "
            "WHERE h.project_id = ? ORDER BY d.role",
            [project_id],
        )
        scene_numbers = await self._head_episode_numbers(project_id, "sceneList")
        draft_numbers = await self._head_episode_numbers(
            project_id,
            "screenplayDraft",
        )
        return {
            "project": {
                "id": project_id,
                "title": str(project.get("title") or ""),
                "format": project.get("format"),
                "source": _planning_source(project.get("source")),
                "brief": {
                    "approach": _clip(
                        str((project.get("brief") or {}).get("approach") or ""),
                        800,
                    ),
                    "premise": _clip(
                        str((project.get("brief") or {}).get("premise") or ""),
                        1_200,
                    ),
                },
                "stage": str(project.get("stage") or workflow.get("stage") or ""),
            },
            "workflow": {"stage": str(workflow.get("stage") or "")},
            "availableDeliverables": list(dict.fromkeys(
                str(item.get("role") or "")
                for item in (workspace.get("deliverables") or ())
                if isinstance(item, Mapping) and str(item.get("role") or "")
            )),
            "acceptedDeliverables": [{
                "role": str(head["role"]),
                "status": "accepted",
                "summary": _planning_summary(head.get("summary_json")),
            } for head in heads],
            "candidateDeliverables": [
                _planning_candidate(item)
                for item in (workspace.get("candidates") or ())[:3]
                if isinstance(item, Mapping)
            ],
            "episodeState": {
                "sceneListEpisodeNumbers": list(scene_numbers),
                "draftEpisodeNumbers": list(draft_numbers),
                "remainingEpisodeNumbers": [
                    number for number in scene_numbers if number not in draft_numbers
                ],
            },
        }

    async def revision(
        self,
        revision_id: str,
        *,
        text_limit: int = 16_000,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT r.id, d.role, p.payload_json, p.content_text "
            "FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            "LEFT JOIN screenplay_revision_parts AS p ON p.revision_id = r.id "
            "AND p.part_type = 'document' AND p.part_key = 'main' "
            "WHERE r.id = ?",
            [revision_id],
        )
        if row is None:
            return None
        return {
            "role": str(row["role"]),
            "revisionId": str(row["id"]),
            "content": _object(row.get("payload_json")),
            "contentText": _clip(str(row.get("content_text") or ""), text_limit),
        }

    async def heads(
        self,
        project_id: str,
        *,
        roles: Sequence[str] = (),
        text_limit: int = 16_000,
    ) -> list[dict[str, Any]]:
        params: list[object] = [project_id]
        role_filter = ""
        if roles:
            placeholders = ",".join("?" for _ in roles)
            role_filter = f" AND d.role IN ({placeholders})"
            params.extend(roles)
        rows = await self._db.fetch_all(
            "SELECT d.role, r.id AS revision_id, p.payload_json, p.content_text "
            "FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revisions AS r ON r.id = h.revision_id "
            "LEFT JOIN screenplay_revision_parts AS p "
            "ON p.revision_id = r.id AND p.part_type = 'document' "
            "AND p.part_key = 'main' WHERE h.project_id = ?"
            f"{role_filter} ORDER BY d.role",
            params,
        )
        return [{
            "role": str(row["role"]),
            "revisionId": str(row["revision_id"]),
            "content": _object(row.get("payload_json")),
            "contentText": _clip(str(row.get("content_text") or ""), text_limit),
        } for row in rows]

    async def revisions(
        self,
        revision_ids: Sequence[str],
        *,
        text_limit: int = 16_000,
    ) -> list[dict[str, Any]]:
        result = []
        for revision_id in dict.fromkeys(str(value) for value in revision_ids):
            revision = await self.revision(revision_id, text_limit=text_limit)
            if revision is None:
                raise AppError(f"项目文档版本 {revision_id} 不存在", 409)
            result.append(revision)
        return result

    async def revision_identities(
        self,
        project_id: str,
        revision_ids: Sequence[str],
    ) -> dict[str, str]:
        """Resolve immutable role/id control facts without loading any body."""

        normalized = tuple(dict.fromkeys(
            str(value).strip() for value in revision_ids if str(value).strip()
        ))
        if not normalized:
            return {}
        marks = ",".join("?" for _ in normalized)
        rows = await self._db.fetch_all(
            "SELECT r.id, d.role FROM screenplay_revisions AS r "
            "JOIN screenplay_deliverables AS d ON d.id = r.deliverable_id "
            f"WHERE r.project_id = ? AND r.id IN ({marks})",
            [project_id, *normalized],
        )
        by_id = {str(row["id"]): str(row["role"]) for row in rows}
        missing = set(normalized) - set(by_id)
        if missing:
            raise AppError(
                "项目文档版本不存在或不属于当前剧本项目",
                409,
            )
        result: dict[str, str] = {}
        for revision_id in normalized:
            role = by_id[revision_id]
            if role in result:
                raise AppError(f"剧本任务包含多个 {role} 输入版本", 409)
            result[role] = revision_id
        return result

    async def revision_episode_numbers(
        self,
        revision_id: str,
    ) -> tuple[int, ...]:
        return await self._revision_episode_numbers(revision_id)

    async def revision_episode_digest(
        self,
        revision_id: str,
        episode_number: int,
    ) -> str:
        row = await self._db.fetch_one(
            "SELECT content_digest FROM screenplay_revision_parts "
            "WHERE revision_id = ? AND part_type = 'episode' AND part_key = ?",
            [revision_id, str(int(episode_number))],
        )
        digest = str((row or {}).get("content_digest") or "").strip()
        if not digest:
            raise AppError(
                f"剧本正文版本缺少第 {episode_number} 集内容摘要",
                409,
            )
        return digest.removeprefix("sha256:")

    async def episode_context(
        self,
        project_id: str,
        episode_number: int,
        *,
        draft_revision_id: str | None = None,
        scene_list_revision_id: str | None = None,
    ) -> dict[str, Any]:
        scene = (
            await self._revision_episode(scene_list_revision_id, episode_number)
            if scene_list_revision_id
            else await self._head_episode(
                project_id,
                "sceneList",
                episode_number,
            )
        )
        if scene is None:
            raise AppError(f"场景表中不存在第 {episode_number} 集", 409)
        previous = None
        if episode_number > 1:
            previous = (
                await self._revision_episode(draft_revision_id, episode_number - 1)
                if draft_revision_id
                else await self._head_episode(
                    project_id,
                    "screenplayDraft",
                    episode_number - 1,
                )
            )
        current = (
            await self._revision_episode(draft_revision_id, episode_number)
            if draft_revision_id
            else None
        )
        return {
            "sceneListId": scene["revisionId"],
            "episode": scene["payload"],
            "previousEpisode": previous["payload"] if previous else None,
            "currentDraft": current["payload"] if current else None,
        }

    async def episode_manifest(
        self,
        project_id: str,
        episode_number: int,
    ) -> dict[str, Any]:
        """Load only host validation identity, never prose continuity."""

        scene = await self._head_episode(project_id, "sceneList", episode_number)
        if scene is None:
            raise AppError(f"场景表中不存在第 {episode_number} 集", 409)
        scenes = scene["payload"].get("scenes")
        if not isinstance(scenes, list):
            raise AppError(f"第 {episode_number} 集场景数据不完整", 409)
        scene_ids = tuple(
            str(item.get("id") or "").strip()
            for item in scenes
            if isinstance(item, Mapping)
        )
        if not scene_ids or any(not item for item in scene_ids):
            raise AppError(f"第 {episode_number} 集场景数据不完整", 409)
        return {
            "sceneListId": scene["revisionId"],
            "sceneIds": scene_ids,
        }

    async def head_revision_refs(self, project_id: str) -> tuple[str, ...]:
        rows = await self._db.fetch_all(
            "SELECT h.revision_id FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "WHERE h.project_id = ? ORDER BY d.role",
            [project_id],
        )
        return tuple(str(row["revision_id"]) for row in rows)

    async def draft_revision_manifest(
        self,
        project_id: str,
        draft_revision_id: str,
    ) -> dict[int, tuple[str, ...]]:
        numbers = await self._revision_episode_numbers(draft_revision_id)
        result: dict[int, tuple[str, ...]] = {}
        for number in numbers:
            episode = await self._revision_episode(draft_revision_id, number)
            if episode is None:
                raise AppError(f"剧本正文缺少第 {number} 集", 409)
            payload = episode.get("payload")
            scene_ids = _draft_episode_scene_ids(payload)
            if not scene_ids:
                raise AppError(f"剧本正文第 {number} 集场景身份不完整", 409)
            result[number] = scene_ids
        return result

    async def structure_episode_numbers(
        self,
        project_id: str,
    ) -> tuple[int, ...]:
        return await self._head_episode_numbers(project_id, "structure")

    async def available_episode_numbers(
        self,
        project_id: str,
        *,
        draft_revision_id: str | None = None,
    ) -> dict[str, tuple[int, ...]]:
        scene_numbers = await self._head_episode_numbers(project_id, "sceneList")
        draft_numbers = (
            await self._revision_episode_numbers(draft_revision_id)
            if draft_revision_id
            else await self._head_episode_numbers(project_id, "screenplayDraft")
        )
        return {
            "sceneList": scene_numbers,
            "draft": draft_numbers,
            "remaining": tuple(number for number in scene_numbers if number not in draft_numbers),
        }

    async def source_chapter_identities(
        self,
        project_id: str,
    ) -> tuple[dict[str, Any], ...]:
        """Enumerate authorized leaf identities without loading source prose."""

        project = await self._db.fetch_one(
            "SELECT source_kind, source_book_id, source_scope_json "
            "FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
        if project is None:
            raise NotFoundError("剧本项目不存在")
        if str(project.get("source_kind") or "original") != "book":
            raise AppError("原作分析只适用于已绑定来源作品的项目", 409)
        chapters = await scoped_chapters(self._db, project)
        if not chapters:
            raise AppError("当前授权原作范围没有可分析的正文章节", 409)
        return tuple({
            "id": str(chapter["id"]),
            "title": str(chapter.get("title") or ""),
            "index": int(chapter.get("index") or 0),
        } for chapter in chapters)

    async def _head_episode(
        self,
        project_id: str,
        role: str,
        episode_number: int,
    ) -> dict[str, Any] | None:
        if episode_number <= 0:
            return None
        row = await self._db.fetch_one(
            "SELECT h.revision_id, p.payload_json FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revision_parts AS p ON p.revision_id = h.revision_id "
            "WHERE h.project_id = ? AND d.role = ? AND p.part_type = 'episode' "
            "AND p.part_key = ?",
            [project_id, role, str(episode_number)],
        )
        return None if row is None else {
            "revisionId": str(row["revision_id"]),
            "payload": _object(row.get("payload_json")),
        }

    async def _head_episode_numbers(
        self,
        project_id: str,
        role: str,
    ) -> tuple[int, ...]:
        rows = await self._db.fetch_all(
            "SELECT p.part_key FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
            "JOIN screenplay_revision_parts AS p ON p.revision_id = h.revision_id "
            "WHERE h.project_id = ? AND d.role = ? AND p.part_type = 'episode'",
            [project_id, role],
        )
        return tuple(sorted(
            number for row in rows
            if (number := _positive_int(row.get("part_key"))) is not None
        ))

    async def _revision_episode(
        self,
        revision_id: str,
        episode_number: int,
    ) -> dict[str, Any] | None:
        if episode_number <= 0:
            return None
        row = await self._db.fetch_one(
            "SELECT payload_json FROM screenplay_revision_parts "
            "WHERE revision_id = ? AND part_type = 'episode' AND part_key = ?",
            [revision_id, str(episode_number)],
        )
        return None if row is None else {
            "revisionId": revision_id,
            "payload": _object(row.get("payload_json")),
        }

    async def _revision_episode_numbers(
        self,
        revision_id: str,
    ) -> tuple[int, ...]:
        rows = await self._db.fetch_all(
            "SELECT part_key FROM screenplay_revision_parts "
            "WHERE revision_id = ? AND part_type = 'episode'",
            [revision_id],
        )
        return tuple(sorted(
            number for row in rows
            if (number := _positive_int(row.get("part_key"))) is not None
        ))


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _draft_scene_texts(value: object) -> dict[str, str]:
    draft = value if isinstance(value, Mapping) else {}
    raw_scenes = draft.get("sceneTexts") or draft.get("scenes") or ()
    if not isinstance(raw_scenes, list):
        return {}
    result: dict[str, str] = {}
    for raw in raw_scenes:
        if not isinstance(raw, Mapping):
            continue
        scene_id = str(raw.get("sceneId") or raw.get("id") or "").strip()
        text = str(raw.get("contentText") or raw.get("sceneText") or "").strip()
        if scene_id and text:
            result[scene_id] = _clip(text, 8_000)
    return result


def _draft_episode_scene_ids(value: object) -> tuple[str, ...]:
    draft = value if isinstance(value, Mapping) else {}
    raw_ids = draft.get("sceneIds")
    if isinstance(raw_ids, list):
        result = tuple(str(value or "").strip() for value in raw_ids)
    else:
        result = tuple(_draft_scene_texts(draft))
    return result if result and all(result) and len(result) == len(set(result)) else ()


def _planning_source(value: object) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    result = {
        key: source[key]
        for key in ("type", "bookId", "bookTitle")
        if source.get(key) not in (None, "")
    }
    scope = source.get("scope")
    if isinstance(scope, Mapping):
        result["scope"] = {
            key: scope[key]
            for key in ("mode", "count", "chapterIds", "volumeIds")
            if scope.get(key) not in (None, "", [], ())
        }
    return result


def _planning_summary(value: object) -> dict[str, str | int]:
    source = dict(value) if isinstance(value, Mapping) else _object(value)
    result: dict[str, str | int] = {}
    title = source.get("title")
    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()[:240]
    for key in ("textLength", "fieldCount", "partCount"):
        count = source.get(key)
        if type(count) is int and count >= 0:
            result[key] = count
    return result


def _planning_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": str(value.get("role") or ""),
        "status": str(value.get("applicability") or "candidate"),
        "summary": _planning_summary(value.get("summary")),
    }


__all__ = ["ScreenplayAgentContextQuery"]
