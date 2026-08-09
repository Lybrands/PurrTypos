"""Wire contracts for the native screenplay conversation API."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, model_validator

from schemas.screenplay_v2 import (
    ScreenplayV2DeliverableRole,
    ScreenplayV2Model,
    ScreenplayV2OperationIntentRequest,
)


class ScreenplayConversationRuntimeRequest(ScreenplayV2Model):
    apiKey: SecretStr
    baseURL: str | None = Field(default=None, max_length=2_000)
    apiProvider: str = Field(default="openai", min_length=1, max_length=80)
    locale: str = Field(default="zh-CN", min_length=1, max_length=64)
    options: dict[str, object]
    contextWindow: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_model(self):
        if not self.apiKey.get_secret_value().strip():
            raise ValueError("apiKey must not be empty")
        model = str(self.options.get("model") or "").strip()
        if not model:
            raise ValueError("runtime options.model is required")
        self.apiProvider = self.apiProvider.strip().lower()
        self.locale = self.locale.strip()
        self.baseURL = str(self.baseURL or "").strip() or None
        if self.contextWindow is None:
            inferred_window = str(
                self.options.get("context_window") or ""
            ).strip()
            self.contextWindow = inferred_window or None
        return self


class ScreenplayConversationOperationRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    targetRole: ScreenplayV2DeliverableRole
    intent: ScreenplayV2OperationIntentRequest


class SubmitScreenplayConversationTurnRequest(ScreenplayV2Model):
    sessionId: int = Field(..., ge=1)
    content: str = Field(..., min_length=1, max_length=20_000)
    operation: ScreenplayConversationOperationRequest | None = None
    runtime: ScreenplayConversationRuntimeRequest

    @model_validator(mode="after")
    def normalize_content(self):
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("conversation turn content must not be empty")
        return self

    @property
    def route(self) -> Literal["read_only", "operation"]:
        return "operation" if self.operation is not None else "read_only"


class ResumeScreenplayConversationTurnRequest(ScreenplayV2Model):
    runtime: ScreenplayConversationRuntimeRequest


__all__ = [
    "ScreenplayConversationOperationRequest",
    "ScreenplayConversationRuntimeRequest",
    "ResumeScreenplayConversationTurnRequest",
    "SubmitScreenplayConversationTurnRequest",
]
