from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    bookId: str
    chapterId: Optional[str] = None
    # setting = 设定会话（人物/背景），不绑定章节；缺省为章节会话
    scope: Optional[str] = None


class UpdateSessionTitleRequest(BaseModel):
    title: str
