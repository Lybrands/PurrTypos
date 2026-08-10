"""Model-correctable screenplay tool input failures."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class ScreenplayToolInputError(ValueError):
    """A safe domain validation failure that the model can correct and retry."""

    def __init__(
        self,
        message: str,
        *,
        guidance: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message))
        self.guidance = str(guidance)
        self.details = dict(details or {})

    def to_payload(self) -> dict[str, Any]:
        return {
            "success": False,
            "errorCode": "tool_input_invalid",
            "message": str(self),
            "guidance": self.guidance,
            **self.details,
        }


__all__ = ["ScreenplayToolInputError"]
