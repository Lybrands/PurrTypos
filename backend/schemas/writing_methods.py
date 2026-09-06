from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MethodDraftRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    methodType: Literal["primary", "technique"]
    tags: list[str] = Field(default_factory=list, max_length=24)
    markdown: str = Field(default="", max_length=120_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateMethodDraftRequest(MethodDraftRequest):
    expectedDraftRevision: int = Field(ge=0)


class StatusRequest(BaseModel):
    status: Literal["active", "archived"]


class SchemeDraftRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    memberRevisionIds: list[str] = Field(default_factory=list, max_length=64)


class UpdateSchemeDraftRequest(SchemeDraftRequest):
    expectedDraftRevision: int = Field(ge=0)


class BatchPublishRequest(BaseModel):
    methodIds: list[str] = Field(default_factory=list, max_length=64)
    schemeIds: list[str] = Field(default_factory=list, max_length=64)


class CreateAnalysisCandidatesRequest(BaseModel):
    model_config = {"extra": "forbid"}


class PublishAnalysisCandidatesRequest(BaseModel):
    methodIds: list[str] = Field(min_length=1, max_length=64)


class CreateBookWritingMethodBindingRequest(BaseModel):
    bindingType: Literal["method", "scheme"]
    revisionId: str = Field(min_length=1, max_length=200)


class ReorderBookWritingMethodBindingsRequest(BaseModel):
    bindingIds: list[str] = Field(max_length=128)


class UpgradeBookWritingMethodBindingRequest(BaseModel):
    revisionId: str = Field(min_length=1, max_length=200)
