from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from schemas.common import normalize_locale_tag


class ChatStreamRequest(BaseModel):
    messages: List[Dict[str, Any]]
    apiKey: str
    baseURL: Optional[str] = None
    apiProvider: str = "openai"
    locale: str = Field(default="zh-CN", max_length=64)
    options: Optional[Dict[str, Any]] = None
    # Reserved only so unsupported caller-owned tool contracts can be rejected
    # explicitly by the application mapper instead of being silently ignored.
    tools: Optional[List[Dict[str, Any]]] = None
    sessionId: Optional[int] = None
    # 是否允许 Writing Agent 暴露当前书籍范围内的宿主工具。
    enableAgentTools: bool = False
    bookId: Optional[str] = None
    chapterId: Optional[str] = None
    currentChapterTitle: Optional[str] = None
    # 章节与大纲目录由后端根据 bookId 加载，不接受渲染进程快照。
    associatedChapterIds: Optional[List[str]] = None
    associatedOutlineIds: Optional[List[str]] = None
    # 用户在 AiContextBar 勾选的设定/伏笔 id：后端前置 fetch 后注入 system，
    # 前端不再自行拼接记忆文案。
    selectedMemoryIds: Optional[List[Any]] = None
    selectedForeshadowingIds: Optional[List[Any]] = None
    chatAgentMode: Optional[str] = None
    contextWindow: Optional[str] = None
    @field_validator("locale")
    @classmethod
    def normalize_locale(cls, value: str) -> str:
        return normalize_locale_tag(value)

    @field_validator("bookId")
    @classmethod
    def normalize_book_id(cls, value: Optional[str]) -> Optional[str]:
        """Canonicalize the Writing security scope at the HTTP boundary."""

        normalized = str(value or "").strip()
        return normalized or None

class ListModelsRequest(BaseModel):
    apiKey: str
    baseURL: Optional[str] = None
    apiProvider: str = "openai"


class GenerateTitleRequest(BaseModel):
    apiKey: str
    baseURL: Optional[str] = None
    prompt: str
    apiProvider: str = "openai"
    model: Optional[str] = None


class ResolveToolApprovalRequest(BaseModel):
    approved: bool


class CreateAgentDelegationRequest(BaseModel):
    agentRole: str
    objective: str
    input: Dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    priority: int = 0

    @field_validator("agentRole", "objective")
    @classmethod
    def require_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class CaptureAiErrorReportRequest(BaseModel):
    streamId: str
    agentRunId: Optional[str] = None
    sessionId: Optional[int] = None
    conversationId: Optional[int] = None
    bookId: Optional[str] = None
    chapterId: Optional[str] = None
    source: str = "ai_chat_stream"
    errorCode: Optional[str] = None
    errorMessage: str
    model: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("streamId", "errorMessage")
    @classmethod
    def require_error_report_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


class SubmitAiErrorReportRequest(BaseModel):
    userNote: Optional[str] = Field(default=None, max_length=2000)
