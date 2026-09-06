from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from schemas.common import normalize_locale_tag


class WritingMethodOverrides(BaseModel):
    forceRevisionIds: List[str] = Field(default_factory=list, max_length=64)
    excludeRevisionIds: List[str] = Field(default_factory=list, max_length=64)

    @field_validator("forceRevisionIds", "excludeRevisionIds")
    @classmethod
    def normalize_revision_ids(cls, values: List[str]) -> List[str]:
        normalized = [str(value or "").strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("writing method revision id must not be empty")
        if len(normalized) != len(set(normalized)):
            raise ValueError("writing method revision ids must be unique")
        return normalized


class ChatStreamRequest(BaseModel):
    # Renderer request identity; Writing binds it opaquely for recovery.
    streamId: Optional[str] = Field(default=None, max_length=200)
    # Enhanced Writing clients reserve a durable request receipt before POST.
    # Omitted by legacy clients, which retain the historical stream behavior.
    requestReceiptVersion: Optional[Literal[1]] = None
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
    # 用户在 AiContextBar 勾选的组件记忆、设定与伏笔 id。
    selectedLongTermMemoryIds: Optional[List[str]] = Field(
        default=None,
        max_length=32,
    )
    selectedMemoryIds: Optional[List[Any]] = None
    selectedForeshadowingIds: Optional[List[Any]] = None
    writingMethodOverrides: Optional[WritingMethodOverrides] = None
    chatAgentMode: Optional[str] = None
    planningMode: Optional[Literal["reactive", "planned"]] = None
    contextWindow: Optional[str] = None
    # Enhanced renderer history fence. These immutable IDs are part of the
    # request digest and are rechecked both when reserving and claiming.
    expectedConversationIds: Optional[List[int]] = None
    expectedRunIds: Optional[List[str]] = None
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

    @field_validator("streamId")
    @classmethod
    def normalize_stream_id(cls, value: Optional[str]) -> Optional[str]:
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
    options: Optional[Dict[str, Any]] = None


class ResolveToolApprovalRequest(BaseModel):
    approved: bool


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
