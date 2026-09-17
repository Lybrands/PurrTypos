from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    bookId: str
    chapterId: Optional[str] = None
    # setting = 全局会话（不绑定章节，整本书共享）；缺省为章节会话
    scope: Optional[str] = None


class UpdateSessionTitleRequest(BaseModel):
    title: str


class UpdateSessionPinnedRequest(BaseModel):
    pinned: bool


class ReorderSessionsRequest(BaseModel):
    """按展示顺序传入会话 ID 列表，服务端按序写入 sort_order（0..N-1）。"""
    orderedIds: list[int]
