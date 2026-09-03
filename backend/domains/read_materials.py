"""Host-owned identity and content for a reusable read result."""

from dataclasses import dataclass, field
from typing import Any, Mapping


READ_MATERIAL_SOURCE = "purrtypos.prepared_read"


@dataclass(frozen=True)
class ReadMaterial:
    identity: str
    tool_name: str
    arguments: Mapping[str, Any]
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)
