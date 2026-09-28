from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SaveOutlineRequest(BaseModel):
    title: str
    type: Optional[str] = None
    book_id: Optional[str] = None
    markdown_content: Optional[str] = None
    writing_chapter_id: Optional[str] = None
    parent_outline_id: Optional[str] = None


class UpdateOutlineRequest(BaseModel):
    title: Optional[str] = None
    markdown_content: Optional[str] = None
    book_id: Optional[str] = None
    type: Optional[str] = None
