from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    bookId: str
    chapterId: Optional[str] = None


class UpdateSessionTitleRequest(BaseModel):
    title: str
