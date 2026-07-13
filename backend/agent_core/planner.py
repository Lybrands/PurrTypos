"""Business-agnostic model planner with strict typed normalization."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from agent_core.cancellation import await_with_cancellation
from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    MessageRole,
    ModelInvocation,
    PlannerLimits,
    PlanningCapabilities,
    PlanningKind,
    PlanningResult,
    ReasoningMode,
    StepExecutor,
    StepStatus,
    StepType,
    TaskPlan,
    TaskStep,
    ToolChoiceMode,
    ToolRiskLevel,
)
from agent_core.errors import InvalidPlannerOutputError, UnsupportedModelFeatureError
from agent_core.ports import CancellationSignal, ModelGateway


PLANNER_SYSTEM_PROMPT = """You are the planning component of a host-controlled agent.
Return one JSON object only. Do not use Markdown or explanatory prose.

For a multi-step task, return:
{"needsTodos":true,"title":"short title","goal":"short goal","todos":[
 {"id":"stable-id","title":"short step","type":"read|analyze|write|review",
  "executor":"model|tool","expectedTools":["required for tool steps"],
  "riskLevel":"read|write|destructive"}
]}

Use 1-8 ordered steps. A tool step must name only tools supplied by the host.
Model steps must not name tools. Never create a confirm step; the host tool
policy owns approvals. If no plan is needed, return:
{"needsTodos":false,"reason":"short reason"}
"""


class AgentPlanner:
    def __init__(
        self,
        model_gateway: ModelGateway,
        limits: PlannerLimits = PlannerLimits(),
    ):
        self._model_gateway = model_gateway
        self._limits = limits

    async def create_plan(
        self,
        request: AgentRunRequest,
        capabilities: PlanningCapabilities,
        signal: CancellationSignal | None = None,
    ) -> PlanningResult:
        messages = build_planner_messages(request, capabilities)
        invocation = ModelInvocation(
            request=request.model,
            tools=(),
            tool_choice=ToolChoiceMode.NONE,
            max_output_tokens=self._limits.max_output_tokens,
            reasoning_mode=ReasoningMode.DISABLED,
        )
        try:
            completion = await await_with_cancellation(
                self._model_gateway.complete(messages, invocation, signal),
                signal,
            )
        except UnsupportedModelFeatureError:
            completion = await await_with_cancellation(
                self._model_gateway.complete(
                    messages,
                    ModelInvocation(
                        request=request.model,
                        tools=(),
                        tool_choice=ToolChoiceMode.NONE,
                        max_output_tokens=self._limits.max_output_tokens,
                        reasoning_mode=ReasoningMode.DEFAULT,
                    ),
                    signal,
                ),
                signal,
            )
        raw = parse_planner_output(completion.message.content)
        result = normalize_task_plan(raw, capabilities, self._limits)
        return PlanningResult(
            kind=result.kind,
            plan=result.plan,
            reason=result.reason,
            model=completion.model,
        )


def build_planner_messages(
    request: AgentRunRequest,
    capabilities: PlanningCapabilities,
) -> tuple[AgentMessage, ...]:
    payload = {
        "mode": request.mode or "",
        "userText": request.latest_user_text(),
        "availableTools": sorted(capabilities.available_tool_names),
    }
    return (
        AgentMessage(role=MessageRole.SYSTEM, content=PLANNER_SYSTEM_PROMPT),
        AgentMessage(
            role=MessageRole.USER,
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def parse_planner_output(content: Any) -> Mapping[str, Any]:
    if isinstance(content, Mapping):
        return content
    text = str(content or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S | re.I)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise InvalidPlannerOutputError("planner output is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise InvalidPlannerOutputError("planner output must be a JSON object")
    return value


def normalize_task_plan(
    value: Mapping[str, Any],
    capabilities: PlanningCapabilities,
    limits: PlannerLimits = PlannerLimits(),
) -> PlanningResult:
    if "needsTodos" not in value:
        raise InvalidPlannerOutputError("planner output is missing needsTodos")
    if not _truthy(value.get("needsTodos")):
        plan = TaskPlan(
            title="Direct response",
            goal=None,
            steps=(TaskStep(
                id="respond",
                title="Respond",
                type=StepType.REVIEW,
                executor=StepExecutor.MODEL,
                status=StepStatus.PENDING,
                risk_level=ToolRiskLevel.READ,
            ),),
        )
        return PlanningResult(
            kind=PlanningKind.DIRECT_RESPONSE,
            plan=plan,
            reason=_optional_text(value.get("reason")),
        )

    raw_steps = value.get("todos", value.get("steps"))
    if not isinstance(raw_steps, list) or not (1 <= len(raw_steps) <= limits.max_steps):
        raise InvalidPlannerOutputError(
            f"planner must return 1-{limits.max_steps} steps"
        )

    steps: list[TaskStep] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, Mapping):
            raise InvalidPlannerOutputError("planner step must be an object")
        step_id = _clean_id(raw.get("id"), limits.max_step_id_chars) or f"step-{index + 1}"
        if step_id in seen_ids:
            suffix = index + 1
            base = step_id[: max(1, limits.max_step_id_chars - len(str(suffix)) - 1)]
            step_id = f"{base}-{suffix}"
        seen_ids.add(step_id)
        title = _clean_text(raw.get("title"), limits.max_title_chars) or f"Step {index + 1}"
        try:
            step_type = StepType(str(raw.get("type") or StepType.ANALYZE.value))
            executor = StepExecutor(str(
                raw.get("executor")
                or (StepExecutor.TOOL.value if step_type is StepType.READ else StepExecutor.MODEL.value)
            ))
            risk = ToolRiskLevel(str(raw.get("riskLevel") or ToolRiskLevel.READ.value))
        except ValueError as error:
            raise InvalidPlannerOutputError("planner step contains an unsupported enum") from error
        if step_type is StepType.CONFIRM:
            raise InvalidPlannerOutputError("planner must not create confirm steps")
        if step_type is StepType.READ and executor is not StepExecutor.TOOL:
            raise InvalidPlannerOutputError("read steps must use the tool executor")

        raw_tools = raw.get("expectedTools", raw.get("suggestedTools", []))
        if not isinstance(raw_tools, list):
            raise InvalidPlannerOutputError("planner step tools must be a list")
        suggested = tuple(dict.fromkeys(
            str(name).strip() for name in raw_tools if str(name).strip()
        ))
        if executor is StepExecutor.TOOL:
            if not suggested:
                raise InvalidPlannerOutputError("tool step requires at least one tool")
            unknown = set(suggested) - set(capabilities.available_tool_names)
            if unknown:
                raise InvalidPlannerOutputError("planner requested an unavailable tool")
        elif suggested:
            raise InvalidPlannerOutputError("model steps cannot grant tool access")

        steps.append(TaskStep(
            id=step_id,
            title=title,
            type=step_type,
            executor=executor,
            status=StepStatus.PENDING,
            risk_level=risk,
            suggested_tools=suggested,
            description=_optional_text(raw.get("description")),
        ))

    plan = TaskPlan(
        title=_clean_text(value.get("title"), limits.max_title_chars) or "Plan",
        goal=_clean_text(value.get("goal"), limits.max_goal_chars),
        steps=tuple(steps),
    )
    return PlanningResult(
        kind=PlanningKind.PLANNED,
        plan=plan,
        reason=_optional_text(value.get("reason")),
    )


def build_execution_message(plan: TaskPlan) -> AgentMessage:
    payload = {
        "title": plan.title,
        "goal": plan.goal,
        "steps": [
            {
                "id": step.id,
                "title": step.title,
                "executor": step.executor.value,
                "tools": list(step.suggested_tools),
            }
            for step in plan.steps
        ],
    }
    return AgentMessage(
        role=MessageRole.DEVELOPER,
        content=(
            "Execute only the following host-validated plan. Use real structured "
            "tool calls for tool steps and never call a tool outside the current step.\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        ),
        attributes={"agent_core_plan": True},
    )


def _truthy(value: Any) -> bool:
    return value is True or str(value or "").strip().lower() in {"true", "1", "yes"}


def _clean_id(value: Any, limit: int) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    return text[:limit]


def _clean_text(value: Any, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] or None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
