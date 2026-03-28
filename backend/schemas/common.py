from pydantic import BaseModel
from typing import Generic, TypeVar, Any, Optional

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None
