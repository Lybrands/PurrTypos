from __future__ import annotations

from pydantic import BaseModel


class SaveBookStyleRequest(BaseModel):
    pov: str = ""
    tone: str = ""
    pace: str = ""
    banned_rules: str = ""
    reference_chapter_ids: str = ""
    free_notes: str = ""
