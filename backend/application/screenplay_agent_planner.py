"""Model semantic Planner and authoritative screenplay task resolver."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_agent_service import (
    PlannedScreenplayIntent,
    ResolvedScreenplayTask,
)
from application.screenplay_structured_call import ScreenplayStructuredCallService
from domains.screenplay.project_aggregate import STAGE_TARGET_ROLE
from domains.screenplay_agent.contracts import (
    SCREENPLAY_DELIVERABLE_ROLES,
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayScopeKind,
    ScreenplayStageCommand,
)
from exceptions import AppError


_PLANNER_INSTRUCTION = """你是剧本 Agent 的语义规划器。理解用户真正想做什么，不做创作执行。
你必须只输出一个 JSON 对象，不要 Markdown，不要额外文字：
{
  "action": "answer|create|revise|review",
  "instruction": "忠实且完整的执行指令",
  "scope": {
    "kind": "current_stage|next_episodes|episodes|all_remaining",
    "count": 仅 next_episodes 时使用的正整数,
    "episodeNumbers": 仅 episodes 时使用的正整数数组
  },
  "constraints": ["用户明确提出的限制"],
  "preserve": ["修改时明确要求保持不变的内容"],
  "requestedDeliverable": "sourceAnalysis|creativeBrief|structure|sceneList|screenplayDraft|review" 或 null,
  "reply": 仅 answer 时填写的最终答复，否则为 null
}
规则：
1. 普通问答、讨论、解释是 answer；要求生成内容是 create；要求修改已有内容是 revise；要求审查评价是 review。
2. 自由文本与按钮文案完全同等对待，必须按语义判断，不能依赖固定短语。
3. 用户说“接下来 N 集”时使用 next_episodes 和 count；明确列举集数时使用 episodes。
   修改“完整剧本”“全部已有剧本”时使用 current_stage，不要把审阅问题数量当成集数。
4. 不臆造项目状态。可以参考提供的项目和已接受交付物回答，但不能声称尚未执行的创作已经完成。
5. instruction 必须保留用户意图，不能缩成无意义的动词。
6. 用户说“刚才那版”“上一版”时，结合 candidateDeliverables 判断具体交付物。
7. 若输入含 requiredStageCommand，它是宿主不可变约束；action、requestedDeliverable 和 scope 必须精确一致，不得降级为 answer。
"""

_PLANNER_REPAIR = """上一个输出不符合剧本意图协议。不要重新展开分析，立即输出唯一的合法 JSON 对象；
字段枚举、scope 条件、answer 的 reply 以及非 answer 时 reply=null 都必须严格满足协议。"""

_ANSWER_INSTRUCTION = """你负责直接回答用户关于当前剧本项目的问题。
只依据宿主提供的项目上下文、近期对话和本轮用户消息作答；不调用工具，不声称执行了未发生的操作，
不暴露内部协议、规划 JSON、标识符或推理过程。自然、简洁地直接回答用户。"""


class ModelScreenplayIntentPlanner:
    def __init__(
        self,
        db,
        *,
        composition,
    ) -> None:
        self._context = ScreenplayAgentContextQuery(db)
        self._models = ScreenplayStructuredCallService(
            db,
            composition=composition,
        )

    async def plan(
        self,
        *,
        workspace: Mapping[str, Any],
        history: Sequence[Mapping[str, str]],
        user_content: str,
        stage_command: ScreenplayStageCommand | None,
        runtime,
        session_id: int,
        turn_id: str,
    ) -> PlannedScreenplayIntent:
        project = workspace.get("project") or {}
        project_id = str(project.get("id") or "")
        planning_context = await self._context.planning_context(workspace)
        result = await self._models.run_json(
            runtime=runtime,
            session_id=session_id,
            prompt=user_content,
            system_instruction=_PLANNER_INSTRUCTION,
            user_payload={
                "projectContext": planning_context,
                "recentConversation": list(history[-12:]),
                "userMessage": user_content,
                **(
                    {"requiredStageCommand": stage_command.to_mapping()}
                    if stage_command is not None
                    else {}
                ),
            },
            binding_namespace="screenplay.agent.turn",
            binding_aggregate_id=project_id,
            binding_command_id=turn_id,
            conversation_turn_id=turn_id,
            phase="screenplay_intent_planning",
            repair_instruction=_PLANNER_REPAIR,
            validate=lambda value: _validate_intent(value, stage_command),
        )
        intent = ScreenplayIntent.from_mapping(result.value)
        run_id = result.run_id
        if intent.action is ScreenplayIntentAction.ANSWER:
            public = await self._models.run_public_text(
                runtime=runtime,
                session_id=session_id,
                prompt=user_content,
                system_instruction=_ANSWER_INSTRUCTION,
                user_payload={
                    "projectContext": planning_context,
                    "recentConversation": list(history[-12:]),
                    "userMessage": user_content,
                    "answerIntent": {
                        "instruction": intent.instruction,
                        "constraints": list(intent.constraints),
                    },
                },
                binding_namespace="screenplay.agent.turn.response",
                binding_aggregate_id=project_id,
                binding_command_id=f"{turn_id}:response",
                phase="screenplay_answer",
                conversation_turn_id=turn_id,
            )
            intent = replace(intent, reply=public.text)
            run_id = public.run_id
        return PlannedScreenplayIntent(
            intent=intent,
            run_id=run_id,
        )


class SqliteScreenplayTaskResolver:
    """Turn model semantics into legal work using only persisted project truth."""

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


def _validate_intent(
    value: dict[str, Any],
    stage_command: ScreenplayStageCommand | None,
) -> dict[str, Any]:
    intent = ScreenplayIntent.from_mapping(value)
    if stage_command is not None:
        stage_command.require_compatible(intent)
    return intent.to_mapping()


__all__ = ["ModelScreenplayIntentPlanner", "SqliteScreenplayTaskResolver"]
