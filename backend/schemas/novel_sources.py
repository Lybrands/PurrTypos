from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from schemas.screenplay_agent import ScreenplayAgentRuntimeRequest


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
    rightsConfirmed: bool
    modelDataBoundaryConfirmed: bool
    sections: list[SourceSectionLayoutRequest] | None = Field(default=None, max_length=100_000)


class FreezeBookSourceRequest(BaseModel):
    bookId: str = Field(min_length=1, max_length=200)


class ArchiveSourceWorkRequest(BaseModel):
    status: Literal["archived"] = "archived"


class StartNovelAnalysisRequest(BaseModel):
    runtime: ScreenplayAgentRuntimeRequest
    prompt: str = Field(
        default="保留故事概览与事实脉络，蒸馏可执行的写作方法并检验迁移效果。",
        min_length=1,
        max_length=20_000,
    )


class FollowUpNovelAnalysisRequest(BaseModel):
    runtime: ScreenplayAgentRuntimeRequest
    artifactId: str = Field(min_length=1, max_length=300)
    prompt: str = Field(min_length=1, max_length=20_000)


class ResumeNovelAnalysisRequest(BaseModel):
    runtime: ScreenplayAgentRuntimeRequest
    retryFailed: bool = False


class PauseNovelAnalysisRequest(BaseModel):
    expectedTaskRevision: int | None = Field(default=None, ge=1)


class NovelAnalysisEvidenceRequest(BaseModel):
    sectionId: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=20_000)
    segmentStartCharacter: int | None = Field(default=None, ge=0, le=10_000_000)
    segmentEndCharacter: int | None = Field(default=None, gt=0, le=10_000_000)


class NovelAnalysisFactRequest(BaseModel):
    factKind: str = Field(min_length=1, max_length=100)
    subjectKey: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=300)
    value: Any
    lifecycleStatus: str = Field(default="active", min_length=1, max_length=100)
    evidence: list[NovelAnalysisEvidenceRequest] = Field(min_length=1)


class NovelAnalysisCraftCardRequest(BaseModel):
    cardKind: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    bodyMarkdown: str = Field(min_length=1, max_length=100_000)
    evidence: list[NovelAnalysisEvidenceRequest] = Field(min_length=1)


class NovelAnalysisStoryOverviewRequest(BaseModel):
    summaryMarkdown: str = Field(min_length=1, max_length=100_000)
    evidence: list[NovelAnalysisEvidenceRequest] = Field(min_length=1)


class ReviewNovelAnalysisRequest(BaseModel):
    facts: list[NovelAnalysisFactRequest]
    craftCards: list[NovelAnalysisCraftCardRequest]
    storyOverview: NovelAnalysisStoryOverviewRequest | None = None
