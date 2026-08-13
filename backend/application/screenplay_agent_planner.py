"""Compatibility surface for the legacy pre-Root screenplay service.

Task admission uses :mod:`application.screenplay_task_resolver`. The model
planner remains only until Task 5 moves conversation turns onto one Root Run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from application.screenplay_agent_context import ScreenplayAgentContextQuery
from application.screenplay_agent_service import PlannedScreenplayIntent
from application.screenplay_structured_call import ScreenplayStructuredCallService
from application.screenplay_task_resolver import SqliteScreenplayTaskResolver
from domains.screenplay_agent.contracts import (
    ScreenplayIntent,
    ScreenplayIntentAction,
    ScreenplayStageCommand,
)


# TODO(screenplay-root-run Task 5): remove this legacy planner and its second
# answer call when ScreenplayAgentService submits the canonical Root Run.
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
    """Legacy bridge; new turns stop using this class in Task 5."""

    def __init__(self, db, *, composition) -> None:
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
        return PlannedScreenplayIntent(intent=intent, run_id=run_id)


def _validate_intent(
    value: dict[str, Any],
    stage_command: ScreenplayStageCommand | None,
) -> dict[str, Any]:
    intent = ScreenplayIntent.from_mapping(value)
    if stage_command is not None:
        stage_command.require_compatible(intent)
    return intent.to_mapping()


__all__ = ["ModelScreenplayIntentPlanner", "SqliteScreenplayTaskResolver"]
