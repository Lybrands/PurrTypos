from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CanonPreviewRequest(BaseModel):
    sourceRevisionId: str = Field(min_length=1, max_length=200)
    sourceAnalysisId: str = Field(min_length=1, max_length=200)
    forkSectionId: str = Field(min_length=1, max_length=200)


class ContinuationMethodBindingRequest(BaseModel):
    bindingType: Literal["method", "scheme"]
    revisionId: str = Field(min_length=1, max_length=200)


class CreateContinuationRequest(CanonPreviewRequest):
    title: str = Field(min_length=1, max_length=200)
    expectedSnapshotDigest: str = Field(min_length=71, max_length=71)
    enableVolume: bool = False
    writingMethodBindings: list[ContinuationMethodBindingRequest] = Field(
        default_factory=list,
        max_length=100,
    )


__all__ = [
    "CanonPreviewRequest",
    "ContinuationMethodBindingRequest",
    "CreateContinuationRequest",
]
