from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


class NovelAnalysisRuntimeRequest(ScreenplayAgentRuntimeRequest):
    """A saved model identity is required for unattended recovery.

    The secret remains request-scoped.  The id only lets the scheduler
    re-resolve the current local model configuration later.
    """

    modelConfigId: str = Field(min_length=1, max_length=200)


class SourceFilePayload(BaseModel):
    fileName: str = Field(min_length=1, max_length=500)
    extension: Literal[".txt", ".md", ".markdown"]
    content: str = Field(min_length=1, max_length=10_000_000)
    importKind: Literal["file", "folder", "archive"] = "file"
    documentCount: int = Field(default=1, ge=1, le=2_000)
    skippedFileCount: int = Field(default=0, ge=0, le=100_000)


class SourceSectionLayoutRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    startCharacter: int = Field(ge=0, le=10_000_000)
    endCharacter: int = Field(gt=0, le=10_000_000)


class ConfirmSourceImportRequest(SourceFilePayload):
    title: str = Field(default="", max_length=300)
    workId: str | None = Field(default=None, max_length=200)
    expectedContentDigest: str = Field(min_length=64, max_length=64)
    confirmSingleSection: bool = False
    rightsConfirmed: bool = False
    modelDataBoundaryConfirmed: bool = False
    sections: list[SourceSectionLayoutRequest] | None = Field(default=None, max_length=100_000)


class FreezeBookSourceRequest(BaseModel):
    bookId: str = Field(min_length=1, max_length=200)


class ArchiveSourceWorkRequest(BaseModel):
    status: Literal["archived"] = "archived"


class StartNovelAnalysisRequest(BaseModel):
    conversationId: str | None = Field(default=None, max_length=200)
    runtime: NovelAnalysisRuntimeRequest
    prompt: str = Field(
        default="提取人物、世界背景、情节状态和未决线索等创作资料，并提炼可执行的写作技法。",
        min_length=1,
        max_length=20_000,
    )


class FollowUpNovelAnalysisRequest(BaseModel):
    replaceRunId: str | None = Field(default=None, min_length=1, max_length=200)
    conversationId: str | None = Field(default=None, max_length=200)
    runtime: NovelAnalysisRuntimeRequest
    artifactId: str | None = Field(default=None, min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=20_000)


class ResumeNovelAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: NovelAnalysisRuntimeRequest


class PauseNovelAnalysisRequest(BaseModel):
    expectedTaskRevision: int | None = Field(default=None, ge=1)


class NovelAnalysisFactRequest(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    claimNature: str = Field(default="fact", min_length=1, max_length=100)
    factKind: str = Field(min_length=1, max_length=100)
    subjectKey: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=300)
    value: Any
    lifecycleStatus: str = Field(default="active", min_length=1, max_length=100)


class NovelAnalysisCraftCardRequest(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    cardKind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    bodyMarkdown: str = Field(min_length=1, max_length=100_000)


class NovelAnalysisStoryOverviewRequest(BaseModel):
    summaryMarkdown: str = Field(min_length=1, max_length=20_000)


class ReviewNovelAnalysisRequest(BaseModel):
    facts: list[NovelAnalysisFactRequest]
    craftCards: list[NovelAnalysisCraftCardRequest]
    storyOverview: NovelAnalysisStoryOverviewRequest | None = None
    techniqueResult: dict[str, Any]


class AnalysisSessionUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    closed: bool | None = None
