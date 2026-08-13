"""Host-owned scope for one screenplay model/tool Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from purra.contracts import DomainContext
from purra.json_values import thaw_json_mapping

from domains.screenplay_agent.contracts import ScreenplayStageCommand


SCREENPLAY_AGENT_DOMAIN_NAMESPACE = "purrtypos.screenplay"


@dataclass(frozen=True, slots=True)
class ScreenplayAgentDomainContext:
    project_id: str
    turn_id: str | None = None
    stage_command: ScreenplayStageCommand | None = None
    task_id: str | None = None
    unit_id: str | None = None
    target_role: str | None = None
    expected_part_type: str | None = None
    expected_part_key: str | None = None
    tool_access: str = "all"
    source_book_id: str | None = None
    source_scope: Mapping[str, Any] | None = None
    locale: str = "zh-CN"

    def __post_init__(self) -> None:
        project_id = str(self.project_id or "").strip()
        if not project_id:
            raise ValueError("screenplay Agent project_id is required")
        object.__setattr__(self, "project_id", project_id)
        for name in (
            "turn_id",
            "task_id",
            "unit_id",
            "target_role",
            "expected_part_type",
            "expected_part_key",
        ):
            object.__setattr__(
                self,
                name,
                str(getattr(self, name) or "").strip() or None,
            )
        child_fields = (
            self.task_id,
            self.unit_id,
            self.target_role,
            self.expected_part_type,
            self.expected_part_key,
        )
        if self.turn_id is not None:
            if any(value is not None for value in child_fields):
                raise ValueError(
                    "screenplay Agent context must be root or child, not both"
                )
            if self.stage_command is not None and not isinstance(
                self.stage_command,
                ScreenplayStageCommand,
            ):
                raise TypeError("screenplay Agent stage_command is invalid")
        else:
            if self.stage_command is not None or not all(
                value is not None for value in child_fields
            ):
                raise ValueError(
                    "screenplay Agent context must be a complete root or child"
                )
        object.__setattr__(
            self,
            "source_book_id",
            str(self.source_book_id or "").strip() or None,
        )
        object.__setattr__(self, "source_scope", dict(self.source_scope or {}))
        object.__setattr__(self, "locale", str(self.locale or "zh-CN").strip())
        tool_access = str(self.tool_access or "all").strip()
        if tool_access not in {"all", "evidence_read", "candidate_write"}:
            raise ValueError("screenplay Agent tool_access is invalid")
        object.__setattr__(self, "tool_access", tool_access)

    def to_core_context(self) -> DomainContext:
        payload: dict[str, Any] = {
            "projectId": self.project_id,
            "toolAccess": self.tool_access,
            "sourceBookId": self.source_book_id,
            "sourceScope": dict(self.source_scope or {}),
            "locale": self.locale,
        }
        if self.is_root:
            payload.update({
                "turnId": self.turn_id,
                **(
                    {"stageCommand": self.stage_command.to_mapping()}
                    if self.stage_command is not None
                    else {}
                ),
            })
        else:
            payload.update({
                "taskId": self.task_id,
                "unitId": self.unit_id,
                "targetRole": self.target_role,
                "expectedPartType": self.expected_part_type,
                "expectedPartKey": self.expected_part_key,
            })
        return DomainContext(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            payload=payload,
        )

    @property
    def is_root(self) -> bool:
        return self.turn_id is not None

    @property
    def is_child(self) -> bool:
        return self.turn_id is None

    @classmethod
    def from_core_context(
        cls,
        context: DomainContext,
    ) -> "ScreenplayAgentDomainContext":
        if context.namespace != SCREENPLAY_AGENT_DOMAIN_NAMESPACE:
            raise ValueError(
                f"unsupported screenplay Agent namespace: {context.namespace}"
            )
        payload = thaw_json_mapping(context.payload)
        source_scope = payload.get("sourceScope")
        stage_command = payload.get("stageCommand")
        if "stageCommand" in payload and not isinstance(stage_command, Mapping):
            raise ValueError("screenplay Agent stage command must be an object")
        return cls(
            project_id=str(payload.get("projectId") or ""),
            turn_id=str(payload.get("turnId") or "") or None,
            stage_command=(
                ScreenplayStageCommand.from_mapping(stage_command)
                if isinstance(stage_command, Mapping)
                else None
            ),
            task_id=str(payload.get("taskId") or "") or None,
            unit_id=str(payload.get("unitId") or "") or None,
            target_role=str(payload.get("targetRole") or "") or None,
            expected_part_type=(
                str(payload.get("expectedPartType") or "") or None
            ),
            expected_part_key=(
                str(payload.get("expectedPartKey") or "") or None
            ),
            tool_access=str(payload.get("toolAccess") or "all"),
            source_book_id=str(payload.get("sourceBookId") or "") or None,
            source_scope=(
                dict(source_scope) if isinstance(source_scope, Mapping) else {}
            ),
            locale=str(payload.get("locale") or "zh-CN"),
        )


__all__ = [
    "SCREENPLAY_AGENT_DOMAIN_NAMESPACE",
    "ScreenplayAgentDomainContext",
]
