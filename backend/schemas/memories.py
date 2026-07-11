from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AddSparkIdeaRequest(BaseModel):
    bookId: str
    layer: str
    content: str
    chapterId: Optional[str] = None
    characterId: Optional[str] = None


class UpdateSparkIdeaRequest(BaseModel):
    data: Dict[str, Any]


class SearchSparkIdeasRequest(BaseModel):
    bookId: str
    query: str = ""
    options: Optional[Dict[str, Any]] = None


class GetSparkIdeasByBookRequest(BaseModel):
    bookId: str
    layer: Optional[str] = None


class GetSparkIdeasByIdsRequest(BaseModel):
    ids: List[str]


class AddForeshadowingRequest(BaseModel):
    bookId: str
    chapterId: Optional[str] = None
    content: str
    type: Optional[str] = None
    expectedChapterId: Optional[str] = None


class UpdateForeshadowingRequest(BaseModel):
    data: Dict[str, Any]


class GetForeshadowingByBookRequest(BaseModel):
    bookId: str
    status: Optional[str] = None


class GetForeshadowingByIdsRequest(BaseModel):
    ids: List[str]

class ForeshadowingForPromptRequest(BaseModel):
    bookId: str
    query: str = ""
    options: Optional[Dict[str, Any]] = None


class CreateMemoryRequest(BaseModel):
    bookId: str
    kind: str
    content: str
    scopeType: str = "book"
    scopeId: Optional[str] = None
    summary: str = ""
    keywords: str = ""
    importance: int = 3
    confidence: float = 1.0
    status: str = "active"
    pinned: bool = False
    sourceType: str = "manual"
    sourceId: Optional[str] = None


class UpdateMemoryRequest(BaseModel):
    data: Dict[str, Any]


class SearchMemoriesRequest(BaseModel):
    bookId: str
    query: str = ""
    options: Optional[Dict[str, Any]] = None


class GetMemoryByIdsRequest(BaseModel):
    ids: List[str]


class LinkMemoriesRequest(BaseModel):
    bookId: str
    fromMemoryId: int
    toMemoryId: int
    relation: str
    note: str = ""


class BuildMemoryContextRequest(BaseModel):
    bookId: str
    userPrompt: str = ""
    mode: str = ""
    selectedLongTermMemoryIds: Optional[List[Any]] = None
    selectedMemoryIds: Optional[List[Any]] = None
    selectedForeshadowingIds: Optional[List[Any]] = None
    memoryBudget: Optional[int] = None
    memoryRecallLimit: Optional[int] = None
    contextWindow: Optional[str] = None
