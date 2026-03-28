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
    useToolRouter: bool = False
    bookId: Optional[str] = None
    chapterId: Optional[str] = None
    currentChapterTitle: Optional[str] = None
    writingChapters: Optional[List[Any]] = None
    availableOutlines: Optional[List[Any]] = None
    associatedChapterIds: Optional[List[str]] = None
    associatedOutlineIds: Optional[List[str]] = None
    agentMode: Optional[str] = None
    chatAgentMode: Optional[str] = None
    writingMode: str = "default"
    agentActions: Optional[List[str]] = None


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
