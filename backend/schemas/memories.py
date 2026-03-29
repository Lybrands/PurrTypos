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
