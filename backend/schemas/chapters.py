from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel


class ChapterItem(BaseModel):
    id: Optional[str] = None
    title: str
    sort_order: Optional[int] = None
    parent_id: Optional[str] = None


class SaveChaptersRequest(BaseModel):
    chapters: List[ChapterItem]


class AddChapterRequest(BaseModel):
    title: str
    parentId: Optional[str] = None
    isVolume: bool = False


class RenameChapterRequest(BaseModel):
    title: str


class UpdateChapterProgressRequest(BaseModel):
    progress: int
