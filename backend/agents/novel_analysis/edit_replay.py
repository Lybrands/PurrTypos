"""Atomic conversation-suffix replacement for replacement analysis Runs."""

from __future__ import annotations

import json
from dataclasses import dataclass

from exceptions import AppError


_RUN_NAMESPACE = "purrtypos.novel_analysis"
_VISIBLE_NAMESPACES = ("novel_source_analysis", _RUN_NAMESPACE)
_TERMINAL_STATUSES = frozenset({"done", "failed", "canceled"})
_RUN_SESSION_SQL = (
    "COALESCE((SELECT session_id FROM novel_analysis_session_commands "
    "WHERE command_id = r.binding_command_id), ("
    "SELECT owner_sc.session_id FROM ai_agent_long_task_runs current_ltr "
    "JOIN ai_agent_long_task_runs owner_ltr "
    "ON owner_ltr.task_id = current_ltr.task_id "
    "AND owner_ltr.relation = 'created' "
    "JOIN ai_agent_runs owner_run ON owner_run.id = owner_ltr.run_id "
    "JOIN novel_analysis_session_commands owner_sc "
    "ON owner_sc.command_id = owner_run.binding_command_id "
    "WHERE current_ltr.run_id = r.id ORDER BY owner_run.rowid LIMIT 1))"
)


@dataclass(frozen=True, slots=True)
class NovelAnalysisEditTarget:
    run_id: str
    interaction_kind: str
    analysis_artifact_id: str | None


class NovelAnalysisReplacementEditLifecycle:
    """Archive the old suffix only after the replacement Root exists."""

    def __init__(
        self,
        db,
        *,
        source_revision_id: str,
        command_id: str,
        target_run_id: str,
    ) -> None:
        self._db = db
        self._revision_id = source_revision_id
        self._command_id = command_id
        self._target_run_id = target_run_id

    async def inspect(self) -> NovelAnalysisEditTarget:
        target, _ = await self._validate()
        attributes = _mapping(target.get("binding_attributes_json"))
        kind = str(attributes.get("interactionKind") or "analysis")
        if kind not in {"analysis", "follow_up"}:
            raise AppError("编辑目标的交互类型无效", 409)
        artifact_id = str(attributes.get("analysisArtifactId") or "").strip()
        return NovelAnalysisEditTarget(
            run_id=self._target_run_id,
            interaction_kind=kind,
            analysis_artifact_id=artifact_id or None,
        )

    async def validate(self) -> None:
        await self._validate()

    async def before_submit(self) -> None:
        await self._validate()

    async def on_run_started(self, run_id: str) -> None:
        async with self._db.transaction(cancellation_linearizable=True):
            replacement = await self._db.fetch_one(
                "SELECT id FROM ai_agent_runs WHERE id = ? "
                "AND binding_namespace = ? AND binding_aggregate_id = ? "
                "AND binding_command_id = ?",
                [run_id, _RUN_NAMESPACE, self._revision_id, self._command_id],
            )
            if replacement is None:
                raise AppError("编辑重跑的 replacement Run 绑定无效", 409)
            _, suffix = await self._validate()
            for row in suffix:
                await self._db.execute(
                    "INSERT OR IGNORE INTO novel_analysis_superseded_runs "
                    "(run_id, replacement_command_id, target_run_id) "
                    "VALUES (?, ?, ?)",
                    [row["id"], self._command_id, self._target_run_id],
                )

    async def on_run_finished(self, result) -> None:
        del result

    async def on_start_failed(self, code: str) -> None:
        del code

    async def _validate(self):
        session = await self._db.fetch_one(
            "SELECT session_id FROM novel_analysis_session_commands "
            "WHERE command_id = ? AND revision_id = ?",
            [self._command_id, self._revision_id],
        )
        if session is None:
            raise AppError("编辑请求尚未绑定对话", 409)
        target = await self._db.fetch_one(
            "SELECT r.*, r.rowid AS position FROM ai_agent_runs r "
            "WHERE r.id = ? AND r.binding_namespace = ? "
            "AND r.binding_aggregate_id = ? "
            f"AND {_RUN_SESSION_SQL} = ?",
            [
                self._target_run_id,
                _RUN_NAMESPACE,
                self._revision_id,
                session["session_id"],
            ],
        )
        if target is None:
            raise AppError("编辑的消息不属于当前 replacement 对话", 409)
        previous = await self._db.fetch_one(
            "SELECT target_run_id FROM novel_analysis_superseded_runs "
            "WHERE replacement_command_id = ? LIMIT 1",
            [self._command_id],
        )
        if previous is not None and previous["target_run_id"] != self._target_run_id:
            raise AppError("请求已用于编辑另一条消息", 409)
        if previous is None and await self._db.fetch_one(
            "SELECT run_id FROM novel_analysis_superseded_runs WHERE run_id = ?",
            [self._target_run_id],
        ):
            raise AppError("这条消息已被替换，请刷新对话后重试", 409)
        placeholders = ",".join("?" for _ in _VISIBLE_NAMESPACES)
        suffix = await self._db.fetch_all(
            "SELECT r.id, r.status FROM ai_agent_runs r "
            f"WHERE r.binding_namespace IN ({placeholders}) "
            "AND r.binding_aggregate_id = ? AND r.rowid >= ? "
            "AND r.binding_command_id != ? "
            f"AND {_RUN_SESSION_SQL} = ? "
            "AND NOT EXISTS (SELECT 1 FROM novel_analysis_superseded_runs s "
            "WHERE s.run_id = r.id)",
            [
                *_VISIBLE_NAMESPACES,
                self._revision_id,
                target["position"],
                self._command_id,
                session["session_id"],
            ],
        )
        if any(str(row["status"]) not in _TERMINAL_STATUSES for row in suffix):
            raise AppError("请等待当前执行结束或终止后再编辑消息", 409)
        return target, suffix


def _mapping(value) -> dict:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "NovelAnalysisEditTarget",
    "NovelAnalysisReplacementEditLifecycle",
]
