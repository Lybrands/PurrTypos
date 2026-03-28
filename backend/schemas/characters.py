from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel


class CreateCharacterRequest(BaseModel):
    data: Dict[str, Any]


class UpdateCharacterRequest(BaseModel):
    data: Dict[str, Any]


class CharacterOptionRequest(BaseModel):
    category: str
    value: str


class UpdateCharacterOptionRequest(BaseModel):
    value: str
