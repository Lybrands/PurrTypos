"""Native request contract for a screenplay Agent run."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    field_validator,
)
from schemas.common import normalize_locale_tag


ScreenplayId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]
OptionalScreenplayId = Annotated[
    ScreenplayId | None,
    BeforeValidator(lambda value: str(value or "").strip() or None),
]
ScreenplayDraftScope = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=(
            r"^(?:planner|next_scene|next_episode|all_remaining|count|"
            r"next_(?:[2-9]|[1-9]\d|100)_episodes)$"
        ),
    ),
]


class ScreenplayAgentRunRequest(BaseModel):
    """Internal screenplay Run input, independent from Writing chat HTTP."""

    messages: list[dict[str, Any]]
    baseURL: str | None = None
    apiProvider: str = "openai"
    locale: str = Field(default="zh-CN", max_length=64)
    options: dict[str, Any] = Field(default_factory=dict)
    sessionId: int | None = None
    enableAgentTools: bool = False
    chatAgentMode: Literal["ask", "agent"] = "ask"
    contextWindow: str | None = None
    screenplayProjectId: ScreenplayId
    screenplayOperationId: OptionalScreenplayId = None
    sourceBookId: OptionalScreenplayId = None
    activeDocumentId: OptionalScreenplayId = None
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
    screenplayDraftScope: ScreenplayDraftScope = "planner"

    @field_validator("locale")
    @classmethod
    def normalize_locale(cls, value: str) -> str:
        return normalize_locale_tag(value)


__all__ = ["ScreenplayAgentRunRequest"]
