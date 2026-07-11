from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ChatStreamRequest(BaseModel):
    messages: List[Dict[str, Any]]
    apiKey: str
    baseURL: Optional[str] = None
    apiProvider: str = "openai"
    options: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    sessionId: Optional[int] = None
    # 是否在请求中携带 skills/<name>/SKILL.md 解析出的工具列表。
    # 名字曾叫 useToolRouter（误导：实际并无路由，只是"是否加载工具"开关）。
    enableAgentTools: bool = False
    bookId: Optional[str] = None
    chapterId: Optional[str] = None
    currentChapterTitle: Optional[str] = None
    writingChapters: Optional[List[Any]] = None
    availableOutlines: Optional[List[Any]] = None
    associatedChapterIds: Optional[List[str]] = None
    associatedOutlineIds: Optional[List[str]] = None
    # 用户在 AiContextBar 勾选的设定/伏笔 id：后端前置 fetch 后注入 system，
    # 前端不再自行拼接记忆文案。
    selectedMemoryIds: Optional[List[Any]] = None
    selectedForeshadowingIds: Optional[List[Any]] = None
    chatAgentMode: Optional[str] = None
    contextWindow: Optional[str] = None


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
