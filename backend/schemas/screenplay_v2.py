"""Wire contracts for the screenplay project API v2."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScreenplayV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


ScreenplayV2Format = Literal[
    "shortFilm",
    "featureFilm",
    "singleEpisode",
    "series",
    "verticalSeries",
]
ScreenplayV2ScopeMode = Literal[
    "wholeBook",
    "firstChapters",
    "firstVolumes",
    "selectedChapters",
    "selectedVolumes",
]
ScreenplayV2DeliverableRole = Literal[
    "sourceAnalysis",
    "creativeBrief",
    "structure",
    "sceneList",
    "screenplayDraft",
    "review",
]


class ScreenplayV2SourceScopeRequest(ScreenplayV2Model):
    mode: ScreenplayV2ScopeMode = "wholeBook"
    count: int | None = Field(default=None, ge=1, le=10_000)
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
        if self.mode in {"firstChapters", "firstVolumes"} and self.count is None:
            raise ValueError("按前几章或前几卷改编时必须提供 count")
        if self.mode == "selectedChapters" and not self.chapterIds:
            raise ValueError("指定章节改编时必须选择至少一个章节")
        if self.mode == "selectedVolumes" and not self.volumeIds:
            raise ValueError("指定卷改编时必须选择至少一个卷")
        return self


class ScreenplayV2OriginalSourceRequest(ScreenplayV2Model):
    type: Literal["original"]


class ScreenplayV2BookSourceRequest(ScreenplayV2Model):
    type: Literal["book"]
    bookId: str = Field(..., min_length=1, max_length=160)
    scope: ScreenplayV2SourceScopeRequest = Field(
        default_factory=ScreenplayV2SourceScopeRequest
    )

    @model_validator(mode="after")
    def normalize_book_id(self):
        self.bookId = self.bookId.strip()
        return self


ScreenplayV2SourceRequest = Annotated[
    ScreenplayV2OriginalSourceRequest | ScreenplayV2BookSourceRequest,
    Field(discriminator="type"),
]


class ScreenplayV2BriefRequest(ScreenplayV2Model):
    approach: str = Field(default="", max_length=80)
    premise: str = Field(default="", max_length=20_000)

    @model_validator(mode="after")
    def normalize_text(self):
        self.approach = self.approach.strip()
        self.premise = self.premise.strip()
        return self


class CreateScreenplayV2ProjectRequest(ScreenplayV2Model):
    title: str = Field(..., min_length=1, max_length=120)
    format: ScreenplayV2Format = "singleEpisode"
    source: ScreenplayV2SourceRequest
    brief: ScreenplayV2BriefRequest = Field(
        default_factory=ScreenplayV2BriefRequest
    )

    @model_validator(mode="after")
    def normalize_title(self):
        self.title = self.title.strip()
        return self


class UpdateScreenplayV2WorkingCopyRequest(ScreenplayV2Model):
    expectedRevision: int = Field(..., ge=1)
    content: dict[str, object]


class UpdateScreenplayV2ProjectRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    title: str = Field(..., min_length=1, max_length=120)

    @model_validator(mode="after")
    def normalize_title(self):
        self.title = self.title.strip()
        return self


class ChangeScreenplayV2ProjectLifecycleRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)


class DeleteScreenplayV2ProjectRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)


class PublishScreenplayV2WorkingCopyRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    expectedWorkingCopyRevision: int = Field(..., ge=1)


class CreateScreenplayV2WorkingCopyFromRevisionRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    expectedWorkingCopyRevision: int | None = Field(default=None, ge=1)


class AcceptScreenplayV2RevisionRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    confirmInvalidation: bool = False


class ScreenplayV2ReviewDecisionRequest(ScreenplayV2Model):
    issueId: str = Field(..., min_length=1, max_length=200)
    status: Literal[
        "pending",
        "planned",
        "resolved",
        "dismissed",
        "riskAccepted",
    ]
    note: str = Field(default="", max_length=2_000)

    @model_validator(mode="after")
    def normalize_text(self):
        self.issueId = self.issueId.strip()
        self.note = self.note.strip()
        if not self.issueId:
            raise ValueError("审阅意见 ID 不能为空")
        return self


class AdjudicateScreenplayV2ReviewRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    reviewRevisionId: str = Field(..., min_length=1, max_length=200)
    decisions: list[ScreenplayV2ReviewDecisionRequest] = Field(
        ...,
        min_length=1,
        max_length=1_000,
    )

    @model_validator(mode="after")
    def normalize_and_validate_decisions(self):
        self.reviewRevisionId = self.reviewRevisionId.strip()
        issue_ids = [decision.issueId for decision in self.decisions]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("同一批审阅意见不能重复")
        return self


class FinalizeScreenplayV2ProjectRequest(ScreenplayV2Model):
    expectedProjectRevision: int = Field(..., ge=1)
    draftRevisionId: str = Field(..., min_length=1, max_length=200)
    reviewRevisionId: str = Field(..., min_length=1, max_length=200)

    @model_validator(mode="after")
    def normalize_revision_ids(self):
        self.draftRevisionId = self.draftRevisionId.strip()
        self.reviewRevisionId = self.reviewRevisionId.strip()
        return self


__all__ = [
    "AdjudicateScreenplayV2ReviewRequest",
    "AcceptScreenplayV2RevisionRequest",
    "ChangeScreenplayV2ProjectLifecycleRequest",
    "CreateScreenplayV2ProjectRequest",
    "CreateScreenplayV2WorkingCopyFromRevisionRequest",
    "DeleteScreenplayV2ProjectRequest",
    "FinalizeScreenplayV2ProjectRequest",
    "PublishScreenplayV2WorkingCopyRequest",
    "ScreenplayV2BookSourceRequest",
    "ScreenplayV2BriefRequest",
    "ScreenplayV2OriginalSourceRequest",
    "ScreenplayV2SourceScopeRequest",
    "ScreenplayV2ReviewDecisionRequest",
    "UpdateScreenplayV2ProjectRequest",
    "UpdateScreenplayV2WorkingCopyRequest",
]
