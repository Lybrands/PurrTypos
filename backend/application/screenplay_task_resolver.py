"""Resolve screenplay TaskSpec semantics against persisted project truth."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from domains.screenplay.project_aggregate import STAGE_TARGET_ROLE
from domains.screenplay_agent.contracts import (
    SCREENPLAY_DELIVERABLE_ROLES,
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayScopeKind,
)
from exceptions import AppError


@dataclass(frozen=True, slots=True)
class ResolvedScreenplayTask:
    target_role: str
    episode_numbers: tuple[int, ...] = ()
    base_revision_id: str | None = None
    episode_scene_ids: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    source_revision_refs: tuple[str, ...] = ()
    reviewed_draft_id: str | None = None
    document_sections: tuple[str, ...] = ()


class SqliteScreenplayTaskResolver:
    """Turn model semantics into legal work using persisted project truth."""

    def __init__(self, db) -> None:
        self._context = ScreenplayAgentContextQuery(db)

    async def resolve(
        self,
        *,
        workspace: Mapping[str, Any],
        intent: ScreenplayIntent,
    ) -> ResolvedScreenplayTask:
        project = workspace.get("project") or {}
        workflow = workspace.get("workflow") or {}
        project_id = str(project.get("id") or "")
        stage = str(workflow.get("stage") or project.get("stage") or "")
        target = intent.requested_deliverable or self._default_target(stage, intent)
        roles = {
            str(item.get("role") or "")
            for item in workspace.get("deliverables") or ()
            if isinstance(item, Mapping)
        }
        if target not in SCREENPLAY_DELIVERABLE_ROLES or target not in roles:
            raise AppError("用户请求的交付物不属于当前剧本项目", 409)
        self._validate_prerequisite(
            workflow,
            target,
            source_kind=str((project.get("source") or {}).get("type") or "original"),
        )
        base_revision_id = self._base_revision(workspace, target, intent)
        episodes = (
            await self._resolve_episodes(
                project_id,
                intent,
                draft_revision_id=base_revision_id,
            )
            if target == "screenplayDraft"
            else ()
        )
        source_revision_refs = await self._context.head_revision_refs(project_id)
        episode_scene_ids: dict[int, tuple[str, ...]] = {}
        reviewed_draft_id = None
        document_sections: tuple[str, ...] = ()
        if target == "screenplayDraft":
            for number in episodes:
                manifest = await self._context.episode_manifest(project_id, number)
                episode_scene_ids[number] = tuple(manifest["sceneIds"])
        elif target == "review":
            draft_head = (workflow.get("heads") or {}).get("screenplayDraft")
            reviewed_draft_id = (
                str((draft_head or {}).get("id") or "")
                if isinstance(draft_head, Mapping) else ""
            ) or None
            if reviewed_draft_id is None:
                raise AppError("审阅需要已采纳的剧本正文", 409)
            episode_scene_ids = await self._context.draft_revision_manifest(
                project_id,
                reviewed_draft_id,
            )
        elif target == "sceneList":
            structure_numbers = await self._context.structure_episode_numbers(
                project_id
            )
            document_sections = tuple(
                f"episode-{number}" for number in structure_numbers
            )
        return ResolvedScreenplayTask(
            target_role=target,
            episode_numbers=episodes,
            base_revision_id=base_revision_id,
            episode_scene_ids=episode_scene_ids,
            source_revision_refs=source_revision_refs,
            reviewed_draft_id=reviewed_draft_id,
            document_sections=document_sections,
        )

    @staticmethod
    def _base_revision(
        workspace: Mapping[str, Any],
        target: str,
        intent: ScreenplayIntent,
    ) -> str | None:
        candidate = next((
            item for item in workspace.get("candidates") or ()
            if isinstance(item, Mapping)
            and item.get("role") == target
            and item.get("applicability") == "current"
        ), None)
        head = (workspace.get("workflow") or {}).get("heads", {}).get(target)
        if intent.action is ScreenplayIntentAction.REVISE:
            if isinstance(head, Mapping):
                return str(head.get("id") or "") or None
            if candidate is not None:
                return str(candidate.get("id") or "") or None
            return None
        if target == "screenplayDraft":
            if isinstance(head, Mapping):
                return str(head.get("id") or "") or None
            if candidate is not None:
                return str(candidate.get("id") or "") or None
        return None

    @staticmethod
    def _default_target(stage: str, intent: ScreenplayIntent) -> str:
        if intent.action is ScreenplayIntentAction.REVIEW:
            return "review"
        if intent.action is ScreenplayIntentAction.REVISE and stage == "completed":
            return "screenplayDraft"
        target = STAGE_TARGET_ROLE.get(stage)
        if target is None:
            raise AppError("当前项目已完成，请明确要修改或审查的交付物", 409)
        return target

    @staticmethod
    def _validate_prerequisite(
        workflow: Mapping[str, Any],
        target: str,
        *,
        source_kind: str,
    ) -> None:
        heads = workflow.get("heads") or {}
        prerequisite = {
            "creativeBrief": "sourceAnalysis" if source_kind == "book" else None,
            "structure": "creativeBrief",
            "sceneList": "structure",
            "screenplayDraft": "sceneList",
            "review": "screenplayDraft",
        }.get(target)
        if prerequisite and not heads.get(prerequisite):
            raise AppError(f"请先完成并采纳 {prerequisite}", 409)

    async def _resolve_episodes(
        self,
        project_id: str,
        intent: ScreenplayIntent,
        *,
        draft_revision_id: str | None,
    ) -> tuple[int, ...]:
        available = await self._context.available_episode_numbers(
            project_id,
            draft_revision_id=draft_revision_id,
        )
        scene_numbers = available["sceneList"]
        draft_numbers = available["draft"]
        remaining = available["remaining"]
        scope = intent.scope
        if not scene_numbers:
            raise AppError("已采纳的场景表中没有可创作的集", 409)
        if intent.action is ScreenplayIntentAction.REVISE:
            if not draft_numbers:
                raise AppError("当前没有可修改的已接受剧本正文", 409)
            if scope.kind is ScreenplayScopeKind.EPISODES:
                invalid = set(scope.episode_numbers).difference(draft_numbers)
                if invalid:
                    values = "、".join(str(number) for number in sorted(invalid))
                    raise AppError(f"已接受剧本中不存在第 {values} 集", 409)
                return scope.episode_numbers
            if scope.kind in {
                ScreenplayScopeKind.CURRENT_STAGE,
                ScreenplayScopeKind.ALL_REMAINING,
            }:
                return draft_numbers
            raise AppError("修改已有剧本时，请明确集数或要求修改完整剧本", 409)
        if scope.kind is ScreenplayScopeKind.EPISODES:
            invalid = set(scope.episode_numbers).difference(scene_numbers)
            if invalid:
                values = "、".join(str(number) for number in sorted(invalid))
                raise AppError(f"场景表中不存在第 {values} 集", 409)
            return scope.episode_numbers
        if not remaining:
            raise AppError("所有场景表集数都已有已采纳剧本，请明确要求修改哪些集", 409)
        if scope.kind is ScreenplayScopeKind.ALL_REMAINING:
            return remaining
        count = scope.count if scope.kind is ScreenplayScopeKind.NEXT_EPISODES else 1
        if count is None or count > len(remaining):
            raise AppError(f"当前只剩 {len(remaining)} 集可以继续创作", 409)
        return remaining[:count]


__all__ = ["ResolvedScreenplayTask", "SqliteScreenplayTaskResolver"]
