from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class CreatePromptTemplateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)
    content: str = ""
    sort: Optional[int] = None


class UpdatePromptTemplateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=80)
    content: Optional[str] = None
    sort: Optional[int] = None


class ReorderPromptTemplatesRequest(BaseModel):
    ids: List[int]
