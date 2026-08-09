"""Application input shape shared by product-specific Agent facades."""

from __future__ import annotations

from typing import Any, Protocol, Self


class AgentRunInput(Protocol):
    """Structural boundary required by the generic Run application service.

    Product transports may use independent Pydantic models.  The generic Run
    service only needs these runtime fields plus Pydantic's copy/dump methods;
    it must not require every product to inherit the Writing HTTP request.
    """

    messages: list[dict[str, Any]]
    apiProvider: str
    baseURL: str | None
    contextWindow: str | None

    def model_copy(self, *, update: dict[str, Any] | None = None) -> Self: ...

    def model_dump(self, *args, **kwargs) -> dict[str, Any]: ...


__all__ = ["AgentRunInput"]
