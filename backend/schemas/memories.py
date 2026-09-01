from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictMemoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddSparkIdeaRequest(StrictMemoryRequest):
    bookId: str
    layer: str
    content: str
    chapterId: Optional[str] = None
    characterId: Optional[str] = None


class UpdateSparkIdeaRequest(StrictMemoryRequest):
    bookId: str
    content: Optional[str] = None
    layer: Optional[str] = None
    chapterId: Optional[str] = None
    characterId: Optional[str] = None

    @model_validator(mode="after")
    def require_change(self):
        if self.model_fields_set <= {"bookId"}:
            raise ValueError("at least one spark idea field is required")
        return self


class SparkIdeaSearchOptions(StrictMemoryRequest):
    layers: Optional[List[str]] = None
    chapterId: Optional[str] = None
    limitPerLayer: int = Field(default=5, ge=1, le=50)
    limit: int = Field(default=20, ge=1, le=100)


class SearchSparkIdeasRequest(StrictMemoryRequest):
    bookId: str
    query: str = ""
    options: Optional[SparkIdeaSearchOptions] = None


class AddForeshadowingRequest(StrictMemoryRequest):
    bookId: str
    chapterId: Optional[str] = None
    content: str
    type: Optional[str] = None
    expectedChapterId: Optional[str] = None


class UpdateForeshadowingRequest(StrictMemoryRequest):
    bookId: str
    content: Optional[str] = None
    type: Optional[str] = None
    expectedChapterId: Optional[str] = None
    status: Optional[str] = None
    resolvedChapterId: Optional[str] = None

    @model_validator(mode="after")
    def require_change(self):
        if self.model_fields_set <= {"bookId"}:
            raise ValueError("at least one foreshadowing field is required")
        return self


class ForeshadowingSearchOptions(StrictMemoryRequest):
    limit: int = Field(default=10, ge=1, le=100)
    status: Optional[str] = None


class ForeshadowingForPromptRequest(StrictMemoryRequest):
    bookId: str
    query: str = ""
    options: Optional[ForeshadowingSearchOptions] = None


class CreateMemoryRequest(StrictMemoryRequest):
    bookId: str
    kind: str
    text: str = Field(min_length=1, max_length=20_000)
    operationKey: str = Field(min_length=1, max_length=400)
    scopeType: str = "book"
    scopeId: Optional[str] = None
    summary: str = Field(default="", max_length=500)
    keywords: str = Field(default="", max_length=500)
    importance: int = Field(default=3, ge=1, le=5)
    confidence: float = Field(default=1.0, ge=0, le=1)
    state: Literal["active", "pending"] = "active"
    pinned: bool = False


class UpdateMemoryRequest(StrictMemoryRequest):
    bookId: str
    version: int = Field(ge=1)
    operationKey: str = Field(min_length=1, max_length=400)
    text: Optional[str] = Field(default=None, min_length=1, max_length=20_000)
    kind: Optional[str] = None
    scopeType: Optional[str] = None
    scopeId: Optional[str] = None
    summary: Optional[str] = Field(default=None, max_length=500)
    keywords: Optional[str] = Field(default=None, max_length=500)
    importance: Optional[int] = Field(default=None, ge=1, le=5)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    pinned: Optional[bool] = None

    @model_validator(mode="after")
    def require_change(self):
        if self.text is None and all(
            getattr(self, field) is None
            for field in (
                "kind",
                "scopeType",
                "scopeId",
                "summary",
                "keywords",
                "importance",
                "confidence",
                "pinned",
            )
        ):
            raise ValueError("memory update requires at least one changed field")
        return self


class SetMemoryStateRequest(StrictMemoryRequest):
    bookId: str
    version: int = Field(ge=1)
    operationKey: str = Field(min_length=1, max_length=400)
    state: Literal["active", "pending", "disabled"]
    reason: str = Field(min_length=1, max_length=128)


class MemoryReferenceRequest(StrictMemoryRequest):
    id: str = Field(min_length=1, max_length=512)
    version: int = Field(ge=1)


class LinkMemoriesRequest(StrictMemoryRequest):
    bookId: str
    operationKey: str = Field(min_length=1, max_length=400)
    fromMemory: MemoryReferenceRequest
    toMemory: MemoryReferenceRequest
    relation: Literal["supersedes", "contradicts", "supports", "relates_to"]
    note: str = Field(default="", max_length=1_000)


class ReviewMemoryRequest(StrictMemoryRequest):
    bookId: str
    version: int = Field(ge=1)
    operationKey: str = Field(min_length=1, max_length=400)


class ResolveMemoriesRequest(StrictMemoryRequest):
    bookId: str
    operationKey: str = Field(min_length=1, max_length=400)
    kind: Literal["independent", "duplicate", "supersede", "conflict"]
    items: List[MemoryReferenceRequest] = Field(min_length=1, max_length=32)
    keep: Optional[str] = Field(default=None, max_length=512)
    reviewKey: Optional[str] = Field(default=None, max_length=512)


class DeleteMemoryRequest(StrictMemoryRequest):
    bookId: str
    version: int = Field(ge=1)
    operationKey: str = Field(min_length=1, max_length=400)


class BuildMemoryContextRequest(BaseModel):
    bookId: str
    operationKey: str
    userPrompt: str = ""
    mode: str = ""
    selectedLongTermMemoryIds: Optional[List[Any]] = None
    selectedMemoryIds: Optional[List[Any]] = None
    selectedForeshadowingIds: Optional[List[Any]] = None
    memoryBudget: Optional[int] = None
    memoryRecallLimit: Optional[int] = None
    contextWindow: Optional[str] = None
