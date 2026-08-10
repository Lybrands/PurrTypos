"""Public wire contract for the rewritten screenplay Agent."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator

from schemas.screenplay_v2 import ScreenplayV2Model


class ScreenplayAgentRuntimeRequest(ScreenplayV2Model):
    apiKey: SecretStr
    baseURL: str | None = Field(default=None, max_length=2_000)
    apiProvider: str = Field(default="openai", min_length=1, max_length=80)
    locale: str = Field(default="zh-CN", min_length=1, max_length=64)
    options: dict[str, object]
    contextWindow: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def normalize(self):
        if not self.apiKey.get_secret_value().strip():
            raise ValueError("apiKey must not be empty")
        if not str(self.options.get("model") or "").strip():
            raise ValueError("runtime options.model is required")
        self.apiProvider = self.apiProvider.strip().lower()
        self.baseURL = str(self.baseURL or "").strip() or None
        self.locale = self.locale.strip()
        return self


class SubmitScreenplayAgentTurnRequest(ScreenplayV2Model):
    sessionId: int = Field(..., ge=1)
    content: str = Field(..., min_length=1, max_length=20_000)
    runtime: ScreenplayAgentRuntimeRequest

    @model_validator(mode="after")
    def normalize(self):
        self.content = self.content.strip()
        if not self.content:
            raise ValueError("screenplay Agent content must not be empty")
        return self


class ResumeScreenplayOperationRequest(ScreenplayV2Model):
    expectedOperationRevision: int = Field(..., ge=1)
    runtime: ScreenplayAgentRuntimeRequest


__all__ = [
    "ScreenplayAgentRuntimeRequest",
    "ResumeScreenplayOperationRequest",
    "SubmitScreenplayAgentTurnRequest",
]
