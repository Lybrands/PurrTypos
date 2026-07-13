"""Authoritative Agent Run todo state controller."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any, Callable

from agent_core.contracts import (
    RunCreateParams,
    RunStatus,
    StepExecutor,
    StepStatus,
    StepType,
    TaskStep,
    TaskStepUpdate,
    ToolRiskLevel,
    TraceRecord,
)
from agent_core.events import AgentEvent
from services import task_planner

if TYPE_CHECKING:
    from agent_core.ports import EventSink, RunRepository


logger = logging.getLogger(__name__)


class AgentRunController:
    def __init__(
        self,
        *,
        repository: "RunRepository",
        event_sink: "EventSink",
    ):
        self.repository = repository
        self.event_sink = event_sink
        self.run_id: str | None = None
        self.title = "To-dos"
        self.goal: str | None = None
        self.status = "running"
        self.error: str | None = None
        self._steps: list[dict[str, Any]] = []

    async def start(
        self,
        *,
        session_id: int | None,
        prompt: str,
        mode: str | None,
        key: str,
        api_provider: str,
        planner_options: dict[str, Any],
        chat_agent_mode: str | None,
        available_tool_names: set[str],
        signal: Any = None,
        use_planner: bool = True,
    ) -> None:
        self.run_id = await self.repository.create(RunCreateParams(
            session_id=session_id,
            prompt=prompt,
            mode=mode,
        ))
        if not use_planner:
            await self.record_trace(
                "planner",
                "skipped",
                details={"availableToolCount": len(available_tool_names)},
            )
            await self._emit("agentRunStarted", {
                "runId": self.run_id,
                "status": self.status,
                "title": self.title,
                "goal": self.goal,
            })
            return

        planner_started = perf_counter()
        planner_outcome = "model_plan"
        planner_error: Exception | None = None
        try:
            plan = await task_planner.generate_model_task_plan(
                key=key,
                api_provider=api_provider,
                planner_options=planner_options,
                user_text=prompt,
                chat_agent_mode=chat_agent_mode,
                available_tool_names=available_tool_names,
                signal=signal,
            )
        except Exception as exc:
            logger.exception("[agent-run] planner request failed")
            plan = None
            planner_error = exc
            planner_outcome = "exception"
        if not plan:
            if planner_outcome == "model_plan":
                planner_outcome = "invalid_plan"
            error = (
                "Agent 计划生成失败，已停止执行，未开放任何工具。"
                "请重试；若持续出现，请检查当前模型是否支持 JSON 规划。"
            )
            self.title = "计划生成失败"
            self._steps = []
            details: dict[str, Any] = {
                "stepCount": 0,
                "availableToolCount": len(available_tool_names),
                "plannedToolCount": 0,
            }
            if planner_error is not None:
                details["errorType"] = type(planner_error).__name__
                details["error"] = _safe_error_detail(planner_error)
            await self.record_trace(
                "planner",
                planner_outcome,
                details=details,
                duration_ms=round((perf_counter() - planner_started) * 1000),
            )
            await self._emit("agentRunStarted", {
                "runId": self.run_id,
                "status": self.status,
                "title": self.title,
                "goal": self.goal,
            })
            await self.fail(error=error)
            return

        self.title = str(plan.get("title") or "To-dos")
        self.goal = _optional_str(plan.get("goal"))
        self._steps = _initialize_steps(list(plan.get("steps") or []))
        await self.record_trace(
            "planner",
            planner_outcome,
            details={
                "stepCount": len(self._steps),
                "availableToolCount": len(available_tool_names),
                "plannedToolCount": len(self.allowed_tool_names()),
            },
            duration_ms=round((perf_counter() - planner_started) * 1000),
        )
        await self.repository.replace_steps(
            self.run_id,
            [_contract_step(step) for step in self._steps],
        )
        await self._emit("agentRunStarted", {
            "runId": self.run_id,
            "status": self.status,
            "title": self.title,
            "goal": self.goal,
        })
        await self._emit("agentRunTodosUpdated", self._todos_payload())

    async def apply_runtime_todos(
        self,
        payload: dict[str, Any],
        *,
        available_tool_names: set[str],
    ) -> None:
        if not self.run_id:
            return
        raw = dict(payload)
        raw["needsTodos"] = True
        if "todos" not in raw and "steps" in raw:
            raw["todos"] = raw.get("steps")
        plan = task_planner.normalize_model_task_plan(
            raw,
            available_tool_names=available_tool_names,
        )
        if not plan:
            return

        self.title = str(plan.get("title") or self.title or "To-dos")
        self.goal = _optional_str(plan.get("goal")) or self.goal
        self._steps = _merge_runtime_steps(self._steps, list(plan.get("steps") or []))
        await self.repository.replace_steps(
            self.run_id,
            [_contract_step(step) for step in self._steps],
        )
        await self._emit("agentRunTodosUpdated", self._todos_payload())

    async def on_tool_calls_started(self, tool_names: list[str] | None = None) -> None:
        if not self.run_id:
            return
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "running" and _is_model_step(step):
                await self._set_step_status(
                    idx,
                    "done",
                    result_summary=step.get("resultSummary") or "已完成推理，开始执行计划内工具。",
                )
        idx = self._find_running(lambda step: _is_tool_step(step))
        if idx < 0:
            idx = self._next_incomplete_index()
        if idx >= 0 and _is_tool_step(self._steps[idx]):
            await self._set_step_status(idx, "running")

    async def on_tool_round_completed(self) -> None:
        if not self.run_id:
            return
        changed = False
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "running" and _is_tool_step(step):
                await self._set_step_status(
                    idx,
                    "done",
                    result_summary=step.get("resultSummary") or "已完成相关上下文读取。",
                )
                changed = True
        if changed:
            await self._mark_next_runnable()

    async def on_model_delta(self) -> None:
        if not self.run_id:
            return
        # A tool step remains running until its own completion callback arrives.
        if any(step.get("status") == "running" and _is_tool_step(step) for step in self._steps):
            return
        idx = self._next_incomplete_index()
        if idx >= 0 and _is_model_step(self._steps[idx]):
            await self._set_step_status(idx, "running")

    async def complete(self, *, final_response: str = "") -> None:
        if not self.run_id or self.status != "running":
            return
        # One final model response can legitimately cover several consecutive
        # model-only plan steps (analyze -> review -> answer).  They are logical
        # phases, not separate provider calls.  A still-pending tool/confirm
        # step, however, means execution really stopped early.
        has_incomplete_non_model_step = any(
            step.get("status") in {"pending", "running"}
            and not _is_model_step(step)
            for step in self._steps
        )
        blocked = has_incomplete_non_model_step
        for idx, step in enumerate(list(self._steps)):
            status = step.get("status")
            if status not in {"pending", "running"}:
                continue
            if _is_model_step(step) and (
                status == "running" or not has_incomplete_non_model_step
            ):
                await self._set_step_status(
                    idx,
                    "done",
                    result_summary=(
                        step.get("resultSummary")
                        or "最终回答已覆盖该模型步骤。"
                    ),
                )
            else:
                await self._set_step_status(
                    idx,
                    "blocked",
                    result_summary=step.get("resultSummary") or "本轮未执行该计划步骤。",
                )
        if blocked:
            await self.repository.transition(self.run_id, RunStatus.BLOCKED)
            self.status = "blocked"
            await self.record_trace("terminal", "blocked")
            await self._emit("agentRunBlocked", {"runId": self.run_id, "status": self.status})
            return

        await self.repository.transition(
            self.run_id,
            RunStatus.DONE,
            final_response=final_response,
        )
        self.status = "done"
        await self.record_trace(
            "terminal",
            "done",
            details={"finalResponseLength": len(final_response or "")},
        )
        await self._emit("agentRunCompleted", {"runId": self.run_id, "status": self.status})

    async def fail(self, *, error: str) -> None:
        if not self.run_id or self.status != "running":
            return
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "running":
                await self._set_step_status(idx, "failed", error=error)
        await self.repository.transition(self.run_id, RunStatus.FAILED, error=error)
        self.status = "failed"
        self.error = error
        await self.record_trace(
            "terminal",
            "failed",
            details={"errorLength": len(error or "")},
        )
        await self._emit("agentRunFailed", {"runId": self.run_id, "status": self.status, "error": error})

    async def cancel(self, *, reason: str = "request_canceled") -> None:
        """Persist a safe terminal state without replaying unfinished work."""
        if not self.run_id or self.status != "running":
            return
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") in {"pending", "running"}:
                await self._set_step_status(
                    idx,
                    "blocked",
                    result_summary=step.get("resultSummary") or "本轮已中止，未继续执行。",
                )
        await self.repository.transition(self.run_id, RunStatus.CANCELED)
        self.status = "canceled"
        self.error = reason
        await self.record_trace(
            "terminal",
            "canceled",
            details={"reason": str(reason or "request_canceled")[:120]},
        )
        await self._emit("agentRunCanceled", {
            "runId": self.run_id,
            "status": self.status,
            "reason": reason,
        })

    async def record_trace(
        self,
        stage: str,
        outcome: str,
        *,
        details: dict[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if not self.run_id:
            return
        await self.repository.append_trace(
            self.run_id,
            TraceRecord(
                stage=stage,
                outcome=outcome,
                details=details or {},
                duration_ms=duration_ms,
            ),
        )

    async def current_steps(self) -> list[dict[str, Any]]:
        if not self.run_id:
            return []
        # Return memory state so fields not present in the current DB schema remain available to UI.
        return [dict(step) for step in self._steps]

    def current_plan(self) -> dict[str, Any] | None:
        if not self.run_id:
            return None
        return {
            "runId": self.run_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "steps": [dict(step) for step in self._steps],
        }

    def allowed_tool_names(self) -> set[str]:
        """Return the plan's executable tool scope for the central executor."""
        return {
            str(tool).strip()
            for step in self._steps
            for tool in (step.get("suggestedTools") or [])
            if str(tool).strip()
        }

    def allowed_tool_names_for_current_transition(self) -> set[str]:
        """Expose only the running tool step or the next tool transition.

        A running model step may legitimately transition into the immediately
        following tool step by emitting tool calls. Later plan tools remain
        hidden and unauthorized until earlier steps complete.
        """
        running_index = next(
            (
                index
                for index, step in enumerate(self._steps)
                if step.get("status") == "running"
            ),
            -1,
        )
        if running_index < 0:
            return set()

        running = self._steps[running_index]
        if _is_tool_step(running):
            return _step_tool_names(running)
        if not _is_model_step(running):
            return set()

        for step in self._steps[running_index + 1:]:
            if step.get("status") in {"done", "failed", "blocked"}:
                continue
            return _step_tool_names(step) if _is_tool_step(step) else set()
        return set()

    def execution_prompt(self) -> str:
        """Tell the model which already-validated plan it is now executing."""
        tools = sorted(self.allowed_tool_names())
        if not tools:
            return "【本轮执行计划】该计划不需要工具调用；不得调用任何工具。"
        stages = "；".join(
            str(step.get("title") or "")
            for step in self._steps
            if str(step.get("title") or "")
        )
        return (
            "【本轮执行计划】宿主已生成并校验计划："
            f"{stages}。本轮仅允许调用这些计划内工具：{', '.join(tools)}。"
            "宿主会按步骤逐轮暴露工具：只要本轮提供了工具定义，就必须先发出真实的结构化工具调用，"
            "不得用正文、JSON 代码块、XML 或伪调用语法代替工具调用，也不得提前输出最终答案。"
            "若信息不足，请说明缺口，不要尝试调用计划外工具。"
        )

    def _todos_payload(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "steps": [dict(step) for step in self._steps],
        }

    async def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.run_id:
            return
        event = AgentEvent(
            type=event_type,
            run_id=self.run_id,
            payload=payload,
        )
        await self.repository.append_event(self.run_id, event)
        await self.event_sink.emit(event)

    async def _set_step_status(
        self,
        idx: int,
        status: str,
        *,
        result_summary: str | None = None,
        error: str | None = None,
    ) -> None:
        if not self.run_id or idx < 0 or idx >= len(self._steps):
            return
        step = dict(self._steps[idx])
        if (
            step.get("status") == status
            and not result_summary
            and not error
        ):
            return
        step["status"] = status
        if result_summary:
            step["resultSummary"] = result_summary
        if error:
            step["error"] = error
        await self.repository.update_step(
            self.run_id,
            TaskStepUpdate(
                step_id=str(step.get("id") or ""),
                status=StepStatus(status),
                result_summary=result_summary,
                error=error,
            ),
        )
        self._steps[idx] = step
        await self._emit("agentRunTodoUpdated", {
            "runId": self.run_id,
            "stepId": step.get("id"),
            "step": dict(step),
            "status": self.status,
        })

    async def _mark_next_runnable(self) -> None:
        if any(step.get("status") == "running" for step in self._steps):
            return
        idx = self._next_incomplete_index()
        if idx >= 0 and self._steps[idx].get("type") != "confirm":
            await self._set_step_status(idx, "running")

    def _next_incomplete_index(self) -> int:
        return next(
            (
                idx
                for idx, step in enumerate(self._steps)
                if step.get("status") not in {"done", "failed", "blocked"}
            ),
            -1,
        )

    def _find_running(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        return next(
            (
                idx
                for idx, step in enumerate(self._steps)
                if step.get("status") == "running" and predicate(step)
            ),
            -1,
        )

    def _find_next(self, predicate: Callable[[dict[str, Any]], bool]) -> int:
        return next(
            (
                idx
                for idx, step in enumerate(self._steps)
                if step.get("status") not in {"done", "failed", "blocked"} and predicate(step)
            ),
            -1,
        )


def _initialize_steps(raw_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_steps):
        step = dict(raw)
        step["id"] = str(step.get("id") or f"step-{idx + 1}")
        step["title"] = str(step.get("title") or f"步骤 {idx + 1}")
        step["type"] = str(step.get("type") or "analyze")
        step["executor"] = str(step.get("executor") or ("tool" if step["type"] == "read" else "model"))
        step["status"] = "pending" if step.get("status") != "done" else "done"
        steps.append({k: v for k, v in step.items() if v not in (None, "", [])})
    if not any(step.get("status") == "running" for step in steps):
        for idx, step in enumerate(steps):
            if step.get("status") != "done" and step.get("type") != "confirm":
                steps[idx] = {**step, "status": "running"}
                break
    return steps


def _contract_step(step: dict[str, Any]) -> TaskStep:
    """Convert the controller's legacy UI shape at the repository boundary."""
    risk_level = step.get("riskLevel")
    return TaskStep(
        id=str(step.get("id") or ""),
        title=str(step.get("title") or ""),
        type=StepType(str(step.get("type") or StepType.ANALYZE.value)),
        executor=StepExecutor(str(
            step.get("executor")
            or (StepExecutor.TOOL.value if step.get("type") == StepType.READ.value else StepExecutor.MODEL.value)
        )),
        status=StepStatus(str(step.get("status") or StepStatus.PENDING.value)),
        risk_level=(ToolRiskLevel(str(risk_level)) if risk_level else None),
        suggested_tools=tuple(
            str(tool).strip()
            for tool in (step.get("suggestedTools") or [])
            if str(tool).strip()
        ),
        description=_optional_str(step.get("description")),
        result_summary=_optional_str(step.get("resultSummary")),
        error=_optional_str(step.get("error")),
    )


def _merge_runtime_steps(
    current_steps: list[dict[str, Any]],
    raw_steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    current_by_id = {
        str(step.get("id") or ""): dict(step)
        for step in current_steps
        if step.get("id")
    }
    merged: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_steps):
        step = dict(raw)
        step_id = str(step.get("id") or f"step-{idx + 1}")
        previous = current_by_id.get(step_id, {})
        step["id"] = step_id
        step["title"] = str(step.get("title") or previous.get("title") or f"步骤 {idx + 1}")
        step["type"] = str(step.get("type") or previous.get("type") or "analyze")
        step["executor"] = str(
            step.get("executor")
            or previous.get("executor")
            or ("tool" if step["type"] == "read" else "model")
        )
        step["status"] = str(step.get("status") or previous.get("status") or "pending")
        if previous.get("resultSummary") and not step.get("resultSummary"):
            step["resultSummary"] = previous.get("resultSummary")
        merged.append({k: v for k, v in step.items() if v not in (None, "", [])})
    if not any(step.get("status") == "running" for step in merged):
        for idx, step in enumerate(merged):
            if step.get("status") not in {"done", "failed", "blocked"} and step.get("type") != "confirm":
                merged[idx] = {**step, "status": "running"}
                break
    return merged


def _is_tool_step(step: dict[str, Any]) -> bool:
    return step.get("executor") == "tool" or step.get("type") == "read"


def _is_model_step(step: dict[str, Any]) -> bool:
    return step.get("executor") in {None, "model"} and step.get("type") not in {"read", "confirm"}


def _step_tool_names(step: dict[str, Any]) -> set[str]:
    return {
        str(tool).strip()
        for tool in (step.get("suggestedTools") or [])
        if str(tool).strip()
    }


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _safe_error_detail(error: Exception) -> str:
    """Persist enough planner context to diagnose without leaking credentials."""
    text = str(error or "").strip().replace("\r", " ").replace("\n", " ")
    return text[:300]
