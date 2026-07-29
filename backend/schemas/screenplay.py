from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


ScreenplaySourceKind = Literal["book", "original"]
ScreenplayFormat = Literal["短片", "电影", "单集剧", "连续剧", "竖屏短剧"]
ScreenplayStage = Literal[
    "orientation",
    "brief",
    "structure",
    "scenes",
    "draft",
    "review",
    "completed",
]
ScreenplayProjectStatus = Literal["active", "archived"]
ScreenplayDocumentKind = Literal[
    "source_analysis",
    "creative_brief",
    "beat_sheet",
    "episode_outline",
    "scene_list",
    "scene_draft",
    "review",
]
ScreenplayDocumentStatus = Literal["draft", "accepted", "superseded"]
ScreenplaySourceScopeMode = Literal[
    "whole_book",
    "first_chapters",
    "first_volumes",
    "selected_chapters",
    "selected_volumes",
]


class ScreenplaySourceScopeRequest(BaseModel):
    mode: ScreenplaySourceScopeMode = "whole_book"
    count: Optional[int] = Field(None, ge=1, le=10_000)
    chapterIds: list[str] = Field(default_factory=list, max_length=1_000)
    volumeIds: list[str] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_selection(self):
        self.chapterIds = list(dict.fromkeys(
            str(item).strip() for item in self.chapterIds if str(item).strip()
        ))
        self.volumeIds = list(dict.fromkeys(
            str(item).strip() for item in self.volumeIds if str(item).strip()
        ))
        if self.mode in {"first_chapters", "first_volumes"} and self.count is None:
            raise ValueError("按前几章或前几卷改编时必须提供 count")
        if self.mode == "selected_chapters" and not self.chapterIds:
            raise ValueError("指定章节改编时必须选择至少一个章节")
        if self.mode == "selected_volumes" and not self.volumeIds:
            raise ValueError("指定卷改编时必须选择至少一个卷")
        return self


class CreateScreenplayProjectRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    sourceKind: ScreenplaySourceKind
    sourceBookId: Optional[str] = None
    format: ScreenplayFormat = "单集剧"
    approach: str = Field(default="", max_length=80)
    premise: str = Field(default="", max_length=20_000)
    sourceScope: Optional[ScreenplaySourceScopeRequest] = None

    @model_validator(mode="after")
    def validate_source(self):
        source_book_id = str(self.sourceBookId or "").strip()
        if self.sourceKind == "book" and not source_book_id:
            raise ValueError("引用书架作品时必须选择来源书籍")
        if self.sourceKind == "original":
            self.sourceBookId = None
            self.sourceScope = None
        else:
            self.sourceBookId = source_book_id
        self.title = self.title.strip()
        self.approach = self.approach.strip()
        self.premise = self.premise.strip()
        return self


class UpdateScreenplayProjectRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    format: Optional[ScreenplayFormat] = None
    approach: Optional[str] = Field(None, max_length=80)
    premise: Optional[str] = Field(None, max_length=20_000)
    status: Optional[ScreenplayProjectStatus] = None


class CreateScreenplayDocumentRequest(BaseModel):
    kind: ScreenplayDocumentKind
    title: str = Field(..., min_length=1, max_length=160)
    contentJson: dict[str, Any] = Field(default_factory=dict)
    contentText: str = Field(default="", max_length=2_000_000)
    derivedFromIds: list[str] = Field(default_factory=list, max_length=200)
    sourceRunId: Optional[str] = None

    @field_validator("sourceRunId")
    @classmethod
    def normalize_source_run_id(cls, value: Optional[str]) -> Optional[str]:
        normalized = str(value or "").strip()
        return normalized or None


class UpdateScreenplayDocumentRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=160)
    contentJson: Optional[dict[str, Any]] = None
    contentText: Optional[str] = Field(None, max_length=2_000_000)
    derivedFromIds: Optional[list[str]] = Field(None, max_length=200)
