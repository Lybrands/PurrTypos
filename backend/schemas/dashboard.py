from __future__ import annotations

from pydantic import BaseModel


class SetWritingGoalRequest(BaseModel):
    bookId: str
    dailyWords: int = 0
