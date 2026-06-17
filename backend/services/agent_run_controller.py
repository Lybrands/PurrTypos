"""Authoritative Agent Run todo state controller."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from services import task_planner
from services.agent_run_store import (
    append_event,
    block_run,
    complete_run,
    create_run,
    fail_run,
    get_run_todos,
    update_todo_status,
    upsert_todos,
)

if TYPE_CHECKING:
    from database.connection import DatabaseConnection


AgentRunChunkSender = Callable[[dict[str, Any]], None]
logger = logging.getLogger(__name__)


class AgentRunController:
    def __init__(self, *, db: "DatabaseConnection", send_chunk: AgentRunChunkSender):
        self.db = db
        self.send_chunk = send_chunk
        self.run_id: str | None = None
        self.title = "To-dos"
        self.goal: str | None = None
        self.status = "running"
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
    ) -> None:
        self.run_id = await create_run(
            self.db,
            session_id=session_id,
            prompt=prompt,
            mode=mode,
        )
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
        except Exception:
            logger.exception("[agent-run] planner failed, using fallback todos")
            plan = None
        if not plan:
            plan = self._fallback_plan(prompt)

        self.title = str(plan.get("title") or "To-dos")
        self.goal = _optional_str(plan.get("goal"))
        self._steps = _initialize_steps(list(plan.get("steps") or []))
        await upsert_todos(self.db, self.run_id, self._steps)
        await self._emit("agentRunStarted", {
            "runId": self.run_id,
            "status": self.status,
            "title": self.title,
            "goal": self.goal,
        })
        await self._emit("agentRunTodosUpdated", self._todos_payload())

    async def on_tool_calls_started(self, tool_names: list[str] | None = None) -> None:
        if not self.run_id:
            return
        idx = self._find_running(lambda step: _is_tool_step(step))
        if idx < 0:
            idx = self._find_next(lambda step: _is_tool_step(step))
        if idx >= 0:
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
        # If model output starts after a tool round, close any running read step first.
        await self.on_tool_round_completed()
        idx = self._find_running(lambda step: _is_model_step(step))
        if idx < 0:
            idx = self._find_next(lambda step: _is_model_step(step))
        if idx >= 0:
            await self._set_step_status(idx, "running")

    async def complete(self, *, final_response: str = "") -> None:
        if not self.run_id:
            return
        blocked = False
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "running":
                await self._set_step_status(
                    idx,
                    "done",
                    result_summary=step.get("resultSummary") or "本轮已完成该步骤。",
                )
            elif step.get("status") == "pending" and step.get("type") == "confirm":
                blocked = True
                await self._set_step_status(
                    idx,
                    "blocked",
                    result_summary=step.get("resultSummary") or "等待你确认后再继续。",
                )
        if blocked:
            self.status = "blocked"
            await block_run(self.db, self.run_id)
            await self._emit("agentRunBlocked", {"runId": self.run_id, "status": self.status})
            return

        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "pending":
                await self._set_step_status(
                    idx,
                    "done",
                    result_summary=step.get("resultSummary") or "本轮已覆盖该步骤。",
                )
        self.status = "done"
        await complete_run(self.db, self.run_id, final_response=final_response)
        await self._emit("agentRunCompleted", {"runId": self.run_id, "status": self.status})

    async def fail(self, *, error: str) -> None:
        if not self.run_id:
            return
        for idx, step in enumerate(list(self._steps)):
            if step.get("status") == "running":
                await self._set_step_status(idx, "failed", error=error)
        self.status = "failed"
        await fail_run(self.db, self.run_id, error=error)
        await self._emit("agentRunFailed", {"runId": self.run_id, "status": self.status, "error": error})

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

    def _fallback_plan(self, prompt: str) -> dict[str, Any]:
        return {
            "title": "To-dos",
            "goal": prompt[:160] if prompt else None,
            "status": "planned",
            "steps": [
                {
                    "id": "understand-goal",
                    "title": "理解目标",
                    "type": "analyze",
                    "executor": "model",
                    "riskLevel": "read",
                },
                {
                    "id": "gather-context",
                    "title": "收集上下文",
                    "type": "read",
                    "executor": "tool",
                    "riskLevel": "read",
                },
                {
                    "id": "respond",
                    "title": "生成回复",
                    "type": "review",
                    "executor": "model",
                    "riskLevel": "read",
                },
            ],
        }

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
        await append_event(self.db, self.run_id, event_type, payload)
        self.send_chunk({event_type: payload})

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
        self._steps[idx] = step
        await update_todo_status(
            self.db,
            self.run_id,
            str(step.get("id") or ""),
            status,
            result_summary=result_summary,
            error=error,
        )
        await self._emit("agentRunTodoUpdated", {
            "runId": self.run_id,
            "stepId": step.get("id"),
            "step": dict(step),
            "status": self.status,
        })

    async def _mark_next_runnable(self) -> None:
        if any(step.get("status") == "running" for step in self._steps):
            return
        idx = self._find_next(lambda step: step.get("type") != "confirm")
        if idx >= 0:
            await self._set_step_status(idx, "running")

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


def _is_tool_step(step: dict[str, Any]) -> bool:
    return step.get("executor") == "tool" or step.get("type") == "read"


def _is_model_step(step: dict[str, Any]) -> bool:
    return step.get("executor") in {None, "model"} and step.get("type") not in {"read", "confirm"}


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
