from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateSettingEntityRequest(BaseModel):
    entityType: str = "location"
    name: str
    tags: str = ""
    profileMd: str = ""


class UpdateSettingEntityRequest(BaseModel):
    baseRevision: str | None = None
    entityType: Optional[str] = None
    name: Optional[str] = None
    tags: Optional[str] = None
    profileMd: Optional[str] = None
