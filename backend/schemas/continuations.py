from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CanonPreviewRequest(BaseModel):
    sourceRevisionId: str = Field(min_length=1, max_length=200)
    sourceAnalysisId: str = Field(min_length=1, max_length=200)
    forkSectionId: str = Field(min_length=1, max_length=200)


class CreateContinuationRequest(CanonPreviewRequest):
    title: str = Field(min_length=1, max_length=200)
    expectedSnapshotDigest: str = Field(min_length=71, max_length=71)
    enableVolume: bool = False
    operationId: str = Field(min_length=1, max_length=200)
    allowWithoutTechniques: bool = False
    useSourceTechniques: bool = True


__all__ = [
    "CanonPreviewRequest",
    "CreateContinuationRequest",
]
