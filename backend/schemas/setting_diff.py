from __future__ import annotations

from pydantic import BaseModel


class CharacterSnapshot(BaseModel):
    name: str = ""
    tags: str = ""
    profileMd: str = ""


class CommitCharacterDiffRequest(BaseModel):
    """提交人物设定 diff：写 characters 表并记录历史。"""

    name: str = ""
    tags: str = ""
    profileMd: str = ""
    before: CharacterSnapshot = CharacterSnapshot()
    after: CharacterSnapshot = CharacterSnapshot()
    source: str = "ai_tool"
    accepted_segments: int = 0
    rejected_segments: int = 0


class CommitBackgroundDiffRequest(BaseModel):
    """提交故事背景 diff：写 story_background 表并记录历史。"""

    content: str = ""
    before_content: str = ""
    after_content: str = ""
    source: str = "ai_tool"
    accepted_segments: int = 0
    rejected_segments: int = 0
