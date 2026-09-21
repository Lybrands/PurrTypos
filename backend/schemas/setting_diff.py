from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CharacterSnapshot(BaseModel):
    name: str = ""
    tags: str = ""
    profileMd: str = ""


class SettingDiffResolution(BaseModel):
    sessionId: int
    agentRunId: str
    proposalId: str
    sessionKey: str
    kind: Literal["character", "background", "entity"]
    title: str
    status: Literal["committed"]
    acceptedSegments: int = 0
    rejectedSegments: int = 0


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
    resolution: SettingDiffResolution | None = None
    baseRevision: str | None = None


class CommitBackgroundDiffRequest(BaseModel):
    """提交故事背景 diff：写 story_background 表并记录历史。"""

    content: str = ""
    before_content: str = ""
    after_content: str = ""
    source: str = "ai_tool"
    accepted_segments: int = 0
    rejected_segments: int = 0
    resolution: SettingDiffResolution | None = None
    baseRevision: str | None = None


class CommitEntityDiffRequest(BaseModel):
    """提交设定实体 diff：写 setting_entities 表并记录历史。"""

    name: str = ""
    tags: str = ""
    profileMd: str = ""
    before: CharacterSnapshot = CharacterSnapshot()
    after: CharacterSnapshot = CharacterSnapshot()
    source: str = "ai_tool"
    accepted_segments: int = 0
    rejected_segments: int = 0
    resolution: SettingDiffResolution | None = None
    baseRevision: str | None = None
