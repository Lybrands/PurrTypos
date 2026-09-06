"""Host-owned scope for one screenplay model/tool Run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from purra.contracts import DomainContext
from purra.json_values import thaw_json_mapping

from domains.screenplay_agent.candidate_projection import (
    parse_candidate_validation_contract,
)

from domains.screenplay_agent.contracts import (
    SCREENPLAY_DELIVERABLE_ROLES,
    ScreenplayStageCommand,
)


SCREENPLAY_AGENT_DOMAIN_NAMESPACE = "purrtypos.screenplay"
SCREENPLAY_TOOL_PROFILES = frozenset({
    "draft_scene",
    "episode_metadata",
    "review_dimension",
    "source_chapter_digest",
    "source_digest_reduction",
    "source_analysis_section",
    "creative_brief_section",
    "series_arc_index",
    "series_arc_phase",
    "episode_plan_index",
    "episode_plan_fragment",
    "character_arcs_index",
    "character_arc_fragment",
    "scene_list_episode",
    "final_response",
})


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
    candidate_validation_contract: Mapping[str, Any] | None = None
    dependency_part_keys: tuple[str, ...] = ()
    deliverable_revision_scope: Mapping[str, str] | None = None
    episode_number: int | None = None
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
        if not isinstance(self.dependency_part_keys, (list, tuple)):
            raise ValueError("screenplay dependency Part keys must be an array")
        if any(
            not isinstance(value, str) for value in self.dependency_part_keys
        ):
            raise ValueError("screenplay dependency Part keys are invalid")
        dependency_part_keys = tuple(
            str(value).strip() for value in self.dependency_part_keys
        )
        if (
            any(not value for value in dependency_part_keys)
            or len(dependency_part_keys) != len(set(dependency_part_keys))
        ):
            raise ValueError("screenplay dependency Part keys are invalid")
        object.__setattr__(
            self,
            "dependency_part_keys",
            dependency_part_keys,
        )
        revision_scope = self.deliverable_revision_scope
        if revision_scope is not None:
            if not isinstance(revision_scope, Mapping):
                raise ValueError("screenplay deliverable Revision scope is invalid")
            normalized_revision_scope = {
                str(role).strip(): str(revision_id).strip()
                for role, revision_id in revision_scope.items()
            }
            if (
                any(
                    role not in SCREENPLAY_DELIVERABLE_ROLES
                    or not revision_id
                    for role, revision_id in normalized_revision_scope.items()
                )
                or len(set(normalized_revision_scope.values()))
                != len(normalized_revision_scope)
            ):
                raise ValueError("screenplay deliverable Revision scope is invalid")
            object.__setattr__(
                self,
                "deliverable_revision_scope",
                normalized_revision_scope,
            )
        if self.episode_number is not None and (
            isinstance(self.episode_number, bool)
            or not isinstance(self.episode_number, int)
            or not 1 <= self.episode_number <= 10_000
        ):
            raise ValueError("screenplay episode scope is invalid")
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
            if self.candidate_validation_contract is not None:
                raise ValueError(
                    "screenplay Root context cannot carry candidate validation"
                )
            if dependency_part_keys:
                raise ValueError(
                    "screenplay Root context cannot carry dependencies"
                )
            if self.deliverable_revision_scope is not None:
                raise ValueError(
                    "screenplay Root context cannot carry deliverable Revision scope"
                )
            if self.episode_number is not None:
                raise ValueError("screenplay Root context cannot carry episode scope")
        else:
            if self.stage_command is not None or not all(
                value is not None for value in child_fields
            ):
                raise ValueError(
                    "screenplay Agent context must be a complete root or child"
                )
        object.__setattr__(
            self,
            "candidate_validation_contract",
            (
                parse_candidate_validation_contract(
                    self.candidate_validation_contract
                )
                if self.candidate_validation_contract is not None
                else None
            ),
        )
        object.__setattr__(
            self,
            "source_book_id",
            str(self.source_book_id or "").strip() or None,
        )
        object.__setattr__(self, "source_scope", dict(self.source_scope or {}))
        object.__setattr__(self, "locale", str(self.locale or "zh-CN").strip())
        tool_access = str(self.tool_access or "all").strip()
        if tool_access not in {
            "all",
            "evidence_read",
            "candidate_write",
            *SCREENPLAY_TOOL_PROFILES,
        }:
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
                "dependencyPartKeys": list(self.dependency_part_keys),
                **(
                    {
                        "deliverableRevisionScope": dict(
                            self.deliverable_revision_scope
                        )
                    }
                    if self.deliverable_revision_scope is not None
                    else {}
                ),
                **(
                    {"boundEpisodeNumber": self.episode_number}
                    if self.episode_number is not None
                    else {}
                ),
                **(
                    {
                        "candidateValidation": dict(
                            self.candidate_validation_contract
                        )
                    }
                    if self.candidate_validation_contract is not None
                    else {}
                ),
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
        candidate_validation = payload.get("candidateValidation")
        dependency_part_keys = payload.get("dependencyPartKeys", [])
        revision_scope = payload.get("deliverableRevisionScope")
        episode_number = payload.get("boundEpisodeNumber")
        if "stageCommand" in payload and not isinstance(stage_command, Mapping):
            raise ValueError("screenplay Agent stage command must be an object")
        if "candidateValidation" in payload and not isinstance(
            candidate_validation,
            Mapping,
        ):
            raise ValueError(
                "screenplay candidate validation contract must be an object"
            )
        if not isinstance(dependency_part_keys, list):
            raise ValueError("screenplay dependency Part keys must be an array")
        if "deliverableRevisionScope" in payload and not isinstance(
            revision_scope,
            Mapping,
        ):
            raise ValueError("screenplay deliverable Revision scope is invalid")
        if "boundEpisodeNumber" in payload and (
            isinstance(episode_number, bool)
            or not isinstance(episode_number, int)
        ):
            raise ValueError("screenplay episode scope is invalid")
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
            candidate_validation_contract=(
                dict(candidate_validation)
                if isinstance(candidate_validation, Mapping)
                else None
            ),
            dependency_part_keys=tuple(dependency_part_keys),
            deliverable_revision_scope=(
                dict(revision_scope)
                if isinstance(revision_scope, Mapping)
                else None
            ),
            episode_number=(
                episode_number if isinstance(episode_number, int) else None
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
    "SCREENPLAY_TOOL_PROFILES",
    "ScreenplayAgentDomainContext",
]
