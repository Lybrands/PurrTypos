from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TechniqueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operationId: str = Field(min_length=1, max_length=512)


class VersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["technique", "scheme"]
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    versionId: str = Field(pattern=r"^[a-f0-9]{64}$")


class CreateTechniqueDraftRequest(TechniqueRequest):
    techniqueId: str | None = None
    fromVersion: VersionRef | None = None


class FileChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["put", "delete", "move"]
    path: str
    content: str | None = None
    target: str | None = None


class ApplyTechniqueChangesRequest(TechniqueRequest):
    expectedDraftRevision: int = Field(ge=0)
    changes: list[FileChange] = Field(min_length=1, max_length=256)


class SealTechniqueRequest(TechniqueRequest):
    expectedDraftRevision: int = Field(ge=0)
    expectedTreeDigest: str = Field(pattern=r"^[a-f0-9]{64}$")


class PublishTechniqueRequest(TechniqueRequest):
    ref: VersionRef
    expectedPublishedHead: str | None


class SchemeContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schemaVersion: Literal[1] = 1
    name: str = Field(max_length=120)
    description: str = Field(max_length=1000)
    composition: str = Field(default="", max_length=262144)
    members: list[VersionRef] = Field(default_factory=list, max_length=64)


class CreateSchemeDraftRequest(TechniqueRequest):
    schemeId: str | None = None
    content: SchemeContent


class UpdateSchemeDraftRequest(TechniqueRequest):
    expectedDraftRevision: int = Field(ge=0)
    content: SchemeContent


class TechniqueStatusRequest(TechniqueRequest):
    status: Literal["active", "archived"]


class TechniqueModeRequest(BaseModel):
    mode: Literal["manual", "auto"]


class ImportTechniqueRequest(TechniqueRequest):
    files: dict[str, str]
    techniqueId: str | None = None


class ReserveTechniqueInputRequest(TechniqueRequest):
    bookId: str
    sessionId: str
    mode: Literal["manual", "auto"] = "manual"
    manual: list[VersionRef] | None = Field(default=None, max_length=64)


class UploadTechniqueRequest(TechniqueRequest):
    files: dict[str, str]
    bookId: str
    sessionId: str


class ImportSchemeRequest(TechniqueRequest):
    bundle: dict


class DeleteTechniqueRequest(TechniqueRequest):
    revisionToken: str = Field(pattern=r"^[a-f0-9]{64}$")


class TechniqueSelectionRequest(BaseModel):
    refs: list[VersionRef] = Field(max_length=64)
