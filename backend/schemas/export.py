from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ExportEpubRequest(BaseModel):
    bookId: str
    chapterIds: Optional[list[str]] = None
