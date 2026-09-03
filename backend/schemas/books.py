from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CreateBookRequest(BaseModel):
    title: str
    enableVolume: Optional[bool] = False


class RenameBookRequest(BaseModel):
    title: str
