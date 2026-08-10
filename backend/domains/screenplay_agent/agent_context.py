"""Host-owned scope for one screenplay model/tool Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from purra.contracts import DomainContext
from purra.json_values import thaw_json_mapping


SCREENPLAY_AGENT_DOMAIN_NAMESPACE = "purrtypos.screenplay"


@dataclass(frozen=True, slots=True)
class ScreenplayAgentDomainContext:
    project_id: str
    task_id: str
    unit_id: str
    target_role: str
    expected_part_type: str
    expected_part_key: str
    source_book_id: str | None = None
    source_scope: Mapping[str, Any] | None = None
    locale: str = "zh-CN"

    def __post_init__(self) -> None:
        for name in (
            "project_id",
            "task_id",
            "unit_id",
            "target_role",
            "expected_part_type",
            "expected_part_key",
        ):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"screenplay Agent {name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(
            self,
            "source_book_id",
            str(self.source_book_id or "").strip() or None,
        )
        object.__setattr__(self, "source_scope", dict(self.source_scope or {}))
        object.__setattr__(self, "locale", str(self.locale or "zh-CN").strip())

    def to_core_context(self) -> DomainContext:
        return DomainContext(
            namespace=SCREENPLAY_AGENT_DOMAIN_NAMESPACE,
            payload={
                "projectId": self.project_id,
                "taskId": self.task_id,
                "unitId": self.unit_id,
                "targetRole": self.target_role,
                "expectedPartType": self.expected_part_type,
                "expectedPartKey": self.expected_part_key,
                "sourceBookId": self.source_book_id,
                "sourceScope": dict(self.source_scope or {}),
                "locale": self.locale,
            },
        )

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
        return cls(
            project_id=str(payload.get("projectId") or ""),
            task_id=str(payload.get("taskId") or ""),
            unit_id=str(payload.get("unitId") or ""),
            target_role=str(payload.get("targetRole") or ""),
            expected_part_type=str(payload.get("expectedPartType") or ""),
            expected_part_key=str(payload.get("expectedPartKey") or ""),
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
