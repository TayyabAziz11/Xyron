"""Common response envelope schemas."""
from __future__ import annotations

from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Standard API response envelope."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = True
    data: T
    message: Optional[str] = None


class PaginatedResponse(BaseModel, Generic[T]):
    """Paginated API response envelope."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = True
    data: List[T]
    total: int
    page: int = 1
    page_size: int = 50
    message: Optional[str] = None
