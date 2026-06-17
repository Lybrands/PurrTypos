from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class SaveConversationRequest(BaseModel):
    """与前端一致：sessionId 为数字；亦接受数字字符串（如 \"12\"）。"""
    sessionId: int
    chapterId: Optional[str] = None
    prompt: str
    response: str
    model: Optional[str] = None
    thinking: Optional[str] = None
    toolCallSegments: Optional[List[Any]] = None
    thinkingBlocks: Optional[List[Any]] = None
    thinkingDurationsMs: Optional[List[Any]] = None
    taskPlan: Optional[Any] = None
    agentRunId: Optional[str] = None
    # 子专家结构化结果：{ role: 'polish'|'review'|'continuation_plan'|'style_unify', payload: any }
    subagentResult: Optional[Any] = None


class DeleteAfterTurnRequest(BaseModel):
    keepTurnCount: int
