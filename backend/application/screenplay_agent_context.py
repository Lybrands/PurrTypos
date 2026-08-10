"""Read-only, bounded context for the rewritten screenplay Agent."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from domains.screenplay.source_scope import scoped_chapters
from exceptions import AppError, NotFoundError
from utils.book_structure import load_chapter_texts


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
            "SELECT d.role, h.revision_id FROM screenplay_project_heads AS h "
            "JOIN screenplay_deliverables AS d ON d.id = h.deliverable_id "
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
            "acceptedDeliverables": [{
                "role": str(head["role"]),
                "revisionId": str(head["revision_id"]),
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

    async def episode_writing_context(
        self,
        project_id: str,
        episode_number: int,
        *,
        draft_revision_id: str | None = None,
        source_revision_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Resolve exact, bounded evidence before any scene model Run."""

        accepted = (
            await self.revisions(source_revision_refs, text_limit=0)
            if source_revision_refs else []
        )
        accepted_by_role = {
            str(item["role"]): item for item in accepted
        }
        scene_list_revision_id = str(
            (accepted_by_role.get("sceneList") or {}).get("revisionId") or ""
        ) or None
        episode = await self.episode_context(
            project_id,
            episode_number,
            draft_revision_id=draft_revision_id,
            scene_list_revision_id=scene_list_revision_id,
        )
        scene_plan = episode.get("episode")
        scenes = (
            scene_plan.get("scenes")
            if isinstance(scene_plan, Mapping)
            else None
        )
        if not isinstance(scenes, list) or not scenes:
            raise AppError(f"第 {episode_number} 集场景数据不完整", 409)
        normalized_scenes = {
            str(scene.get("id") or "").strip(): dict(scene)
            for scene in scenes
            if isinstance(scene, Mapping)
            and str(scene.get("id") or "").strip()
        }
        if len(normalized_scenes) != len(scenes):
            raise AppError(f"第 {episode_number} 集场景数据不完整", 409)

        heads = (
            [
                item for item in accepted
                if item["role"] in {"creativeBrief", "structure", "review"}
            ]
            if accepted else await self.heads(
                project_id,
                roles=("creativeBrief", "structure", "review"),
                text_limit=0,
            )
        )
        by_role = {str(head["role"]): head for head in heads}
        review = by_role.get("review")
        review_content = (
            review.get("content")
            if isinstance(review, Mapping)
            else {}
        )
        review_matches_base = bool(
            draft_revision_id
            and isinstance(review_content, Mapping)
            and str(review_content.get("reviewedDraftId") or "")
            == draft_revision_id
        )
        planned_issue_ids: frozenset[str] = frozenset()
        if review_matches_base and isinstance(review, Mapping):
            decision_rows = await self._db.fetch_all(
                "SELECT issue_id FROM screenplay_review_decisions "
                "WHERE project_id = ? AND review_revision_id = ? "
                "AND status = 'planned' ORDER BY issue_id ASC",
                [project_id, str(review.get("revisionId") or "")],
            )
            planned_issue_ids = frozenset(
                str(row.get("issue_id") or "").strip()
                for row in decision_rows
                if str(row.get("issue_id") or "").strip()
            )
        review_issues = (
            _episode_review_issues(
                review_content,
                frozenset(normalized_scenes),
                allowed_issue_ids=planned_issue_ids,
            )
            if review_matches_base
            else []
        )
        return {
            "sceneListId": str(episode["sceneListId"]),
            "scenePlans": normalized_scenes,
            "currentDraftScenes": _draft_scene_texts(
                episode.get("currentDraft")
            ),
            "previousEpisodeContinuity": _episode_continuity(
                episode.get("previousEpisode")
            ),
            "reviewRevisionId": (
                str(review.get("revisionId") or "")
                if review_matches_base and isinstance(review, Mapping)
                else None
            ),
            "reviewIssues": review_issues,
            "acceptedGuidance": {
                "creativeBrief": _creative_brief_episode_guidance(
                    (by_role.get("creativeBrief") or {}).get("content"),
                    episode_number,
                ),
                "structureEpisode": _structure_episode_guidance(
                    (by_role.get("structure") or {}).get("content"),
                    episode_number,
                ),
            },
        }

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

    async def source_context(self, project_id: str, *, limit: int = 60_000) -> dict[str, Any]:
        project = await self._db.fetch_one(
            "SELECT * FROM screenplay_projects WHERE id = ?",
            [project_id],
        )
        if project is None:
            raise NotFoundError("剧本项目不存在")
        if str(project.get("source_kind") or "original") != "book":
            return {"type": "original", "premise": str(project.get("premise") or "")}
        chapters = await scoped_chapters(self._db, project)
        texts = await load_chapter_texts(
            self._db,
            [str(chapter["id"]) for chapter in chapters],
        )
        remaining = max(1, int(limit))
        selected: list[dict[str, Any]] = []
        for chapter in chapters:
            text = texts.get(str(chapter["id"]), "")
            if remaining <= 0:
                break
            excerpt = _clip(text, remaining)
            selected.append({
                "id": str(chapter["id"]),
                "index": int(chapter.get("index") or 0),
                "title": str(chapter.get("title") or ""),
                "content": excerpt,
            })
            remaining -= len(excerpt)
        return {
            "type": "book",
            "bookId": str(project.get("source_book_id") or ""),
            "chapters": selected,
            "truncated": len(selected) < len(chapters),
        }

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


def _episode_continuity(value: object) -> dict[str, Any] | None:
    episode = value if isinstance(value, Mapping) else {}
    if not episode:
        return None
    scene_texts = _draft_scene_texts(episode)
    final_scene = next(reversed(scene_texts.values()), "")
    return {
        "episodeNumber": _positive_int(episode.get("episodeNumber")),
        "title": str(episode.get("title") or ""),
        "continuitySummary": _clip(
            str(episode.get("continuitySummary") or ""),
            2_000,
        ),
        "finalSceneTail": final_scene[-1_200:],
    }


def _episode_review_issues(
    value: object,
    episode_scene_ids: frozenset[str],
    *,
    allowed_issue_ids: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    review = value if isinstance(value, Mapping) else {}
    raw_issues = review.get("issues")
    if not isinstance(raw_issues, list):
        return []
    result = []
    for raw in raw_issues:
        if not isinstance(raw, Mapping):
            continue
        issue_id = str(raw.get("id") or "").strip()
        if allowed_issue_ids is not None and issue_id not in allowed_issue_ids:
            continue
        issue_scene_ids = tuple(
            str(scene_id or "").strip()
            for scene_id in (raw.get("sceneIds") or ())
            if str(scene_id or "").strip()
        )
        related = tuple(
            scene_id for scene_id in issue_scene_ids
            if scene_id in episode_scene_ids
        )
        if issue_scene_ids and not related:
            continue
        result.append({
            "id": issue_id,
            "severity": str(raw.get("severity") or ""),
            "description": _clip(str(raw.get("description") or ""), 2_000),
            "relatedSceneIds": list(related),
            "crossEpisodeSceneIds": [
                scene_id for scene_id in issue_scene_ids
                if scene_id not in episode_scene_ids
            ],
        })
    return result


def _creative_brief_episode_guidance(
    value: object,
    episode_number: int,
) -> dict[str, Any]:
    content = value if isinstance(value, Mapping) else {}
    raw_brief = content.get("brief")
    brief = raw_brief if isinstance(raw_brief, Mapping) else content
    episode_id = f"ep{episode_number:02d}"
    raw_decisions = brief.get("adaptationDecisions")
    decisions = []
    if isinstance(raw_decisions, list):
        for raw in raw_decisions:
            if not isinstance(raw, Mapping):
                continue
            unit_ids = tuple(
                str(item) for item in (raw.get("structureUnitIds") or ())
            )
            if unit_ids and episode_id not in unit_ids:
                continue
            decisions.append({
                key: raw[key]
                for key in ("id", "subject", "screenIntent", "rationale")
                if raw.get(key) not in (None, "")
            })
    fields = brief.get("fields")
    return {
        "fields": dict(fields) if isinstance(fields, Mapping) else {},
        "adaptationDecisions": decisions,
    }


def _structure_episode_guidance(
    value: object,
    episode_number: int,
) -> dict[str, Any] | None:
    content = value if isinstance(value, Mapping) else {}
    episodes = content.get("episodes")
    if not isinstance(episodes, list):
        return None
    for raw in episodes:
        if (
            isinstance(raw, Mapping)
            and _positive_int(raw.get("number")) == episode_number
        ):
            return dict(raw)
    return None


def _planning_source(value: object) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    return {
        key: source[key]
        for key in ("type", "bookId", "bookTitle")
        if source.get(key) not in (None, "")
    }


def _planning_candidate(value: Mapping[str, Any]) -> dict[str, Any]:
    summary = value.get("summary")
    summary_text = _clip(
        json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
        if isinstance(summary, Mapping)
        else str(summary or ""),
        1_200,
    )
    return {
        "role": str(value.get("role") or ""),
        "revisionId": str(value.get("id") or ""),
        "revisionNo": int(value.get("revisionNo") or 1),
        "parentRevisionId": value.get("parentRevisionId"),
        "summary": summary_text,
    }


__all__ = ["ScreenplayAgentContextQuery"]
