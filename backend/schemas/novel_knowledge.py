from typing import Literal
from pydantic import BaseModel, Field


class StrictModel(BaseModel):
    model_config = {'extra': 'forbid'}


class SelectionRequest(StrictModel):
    selectionToken: str = Field(min_length=1, max_length=12000)


class BindingRequest(SelectionRequest):
    expectedVersion: int = Field(ge=0)
    commandId: str = Field(min_length=1, max_length=100)


class KnowledgeScope(StrictModel):
    purpose: Literal['prose', 'discussion', 'character'] = 'prose'
    chapterId: str | None = Field(default=None, max_length=200)
    characterId: str | None = Field(default=None, max_length=200)


class ConfigureRequest(StrictModel):
    expectedVersion: int = Field(ge=0)
    commandId: str = Field(min_length=1, max_length=100)
    scope: KnowledgeScope | None = None
    semantic: bool | None = None
    unbind: bool = False


class SearchRequest(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal['fulltext', 'hybrid'] = 'hybrid'


class NavigationRequest(StrictModel):
    documentId: str = Field(min_length=1, max_length=200)
    revision: str | None = Field(default=None, max_length=100)
    anchor: str | None = Field(default=None, max_length=500)
