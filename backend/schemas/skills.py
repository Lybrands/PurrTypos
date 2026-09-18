"""Agent-skill library API schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class SkillRequest(BaseModel):
    operationId: str = Field(min_length=1, max_length=512)


class SkillStatusRequest(SkillRequest):
    status: Literal["active", "archived"]


class ImportSkillRequest(SkillRequest):
    files: dict[str, str]


class DeleteSkillRequest(SkillRequest):
    revisionToken: str = Field(pattern=r"^[a-f0-9]{64}$")
