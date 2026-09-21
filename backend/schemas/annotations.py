"""Chapter annotation (批注) request DTOs."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictAnnotationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddAnnotationRequest(StrictAnnotationRequest):
    bookId: str
    chapterId: str
    startOffset: int = Field(ge=0)
    endOffset: int = Field(ge=0)
    quotedText: str
    note: str
    contextBefore: Optional[str] = None
    contextAfter: Optional[str] = None
    source: Literal["manual", "ai"] = "manual"


class UpdateAnnotationRequest(StrictAnnotationRequest):
    bookId: str
    note: Optional[str] = None
    status: Optional[Literal["open", "resolved"]] = None

    @model_validator(mode="after")
    def require_change(self):
        if self.model_fields_set <= {"bookId"}:
            raise ValueError("at least one annotation field is required")
        return self
