from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class SaveConversationRequest(BaseModel):
    """与前端一致：sessionId 为数字；亦接受数字字符串（如 \"12\"）。"""
    sessionId: int
    bookId: Optional[str] = None
    chapterId: Optional[str] = None
    prompt: str
    response: str
    model: Optional[str] = None
    thinking: Optional[str] = None
    toolCallSegments: Optional[List[Any]] = None
    thinkingBlocks: Optional[List[Any]] = None
    thinkingDurationsMs: Optional[List[Any]] = None
    durationMs: Optional[int] = None
    taskPlan: Optional[Any] = None
    agentRunId: Optional[str] = None
