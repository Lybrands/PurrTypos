"""Native request contract for a screenplay Agent run."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from schemas.ai import ChatStreamRequest


class ScreenplayAgentRunRequest(ChatStreamRequest):
    agentProfile: Literal["screenplay"] = "screenplay"
    screenplayProjectId: str
    screenplayOperationId: str | None = None
    sourceBookId: str | None = None
    activeDocumentId: str | None = None
    activeStage: Literal[
        "orientation",
        "brief",
        "structure",
        "scenes",
        "draft",
        "review",
        "completed",
    ] | None = None
    screenplayTaskIntent: Literal["chat", "stage_deliverable"] = "chat"
    screenplayDraftSceneCount: int = Field(default=1, ge=1, le=100)
    screenplayDraftScope: str = "planner"

    @field_validator("screenplayDraftScope")
    @classmethod
    def normalize_screenplay_draft_scope(cls, value: str) -> str:
        normalized = str(value or "planner").strip()
        if normalized in {
            "planner",
            "next_scene",
            "next_episode",
            "all_remaining",
            "count",
        }:
            return normalized
        if re.fullmatch(
            r"next_(?:[2-9]|[1-9]\d|100)_episodes",
            normalized,
        ) is not None:
            return normalized
        raise ValueError("unsupported screenplay draft scope")

    @field_validator(
        "screenplayProjectId",
        "screenplayOperationId",
        "sourceBookId",
        "activeDocumentId",
    )
    @classmethod
    def normalize_screenplay_ids(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @model_validator(mode="after")
    def require_project_scope(self) -> "ScreenplayAgentRunRequest":
        if not self.screenplayProjectId:
            raise ValueError(
                "screenplayProjectId is required for screenplay Agent"
            )
        return self


__all__ = ["ScreenplayAgentRunRequest"]
