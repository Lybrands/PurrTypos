from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SaveStoryBackgroundRequest(BaseModel):
    content: str
