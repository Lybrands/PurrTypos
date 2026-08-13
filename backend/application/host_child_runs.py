"""Generic durable identity contracts for host-orchestrated Child Runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from purra.json_values import freeze_json_mapping
from purra.normalization import positive_int, required_text


HOST_CHILD_BINDING_PROTOCOL = "purra.host-child/v1"


def stable_host_child_key(scope: str, *identity_parts: str) -> str:
    """Build an opaque key from caller-owned persistent business identity."""

    values = [required_text(scope, "host child key scope")]
    values.extend(
        required_text(value, "host child key identity")
        for value in identity_parts
    )
    digest = hashlib.sha256(json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return f"host-child:{digest}"


class HostChildReservationDisposition(StrEnum):
    CREATE = "create"
    WAIT = "wait"
    BOUND = "bound"


class HostChildTerminalRetryPolicy(StrEnum):
    REUSE_DONE_ONLY = "reuse_done_only"
    REUSE_DONE_RETRY_FAILED = "reuse_done_retry_failed"


@dataclass(frozen=True, slots=True)
class HostChildReservation:
    host_child_key: str
    identity_digest: str
    contract: Mapping[str, object]
    generation: int
    attempt_key: str
    owner_token: str | None
    run_id: str | None
    terminal_status: str | None
    disposition: HostChildReservationDisposition

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "host_child_key",
            required_text(self.host_child_key, "host child key"),
        )
        object.__setattr__(
            self,
            "identity_digest",
            required_text(self.identity_digest, "host child identity digest"),
        )
        object.__setattr__(self, "contract", freeze_json_mapping(self.contract))
        object.__setattr__(
            self,
            "generation",
            positive_int(self.generation, "host child generation"),
        )
        object.__setattr__(
            self,
            "attempt_key",
            required_text(self.attempt_key, "host child attempt key"),
        )
        object.__setattr__(
            self,
            "owner_token",
            str(self.owner_token or "").strip() or None,
        )
        object.__setattr__(
            self,
            "run_id",
            str(self.run_id or "").strip() or None,
        )
        object.__setattr__(
            self,
            "terminal_status",
            str(self.terminal_status or "").strip() or None,
        )
        object.__setattr__(
            self,
            "disposition",
            HostChildReservationDisposition(self.disposition),
        )


__all__ = [
    "HOST_CHILD_BINDING_PROTOCOL",
    "stable_host_child_key",
    "HostChildReservation",
    "HostChildReservationDisposition",
    "HostChildTerminalRetryPolicy",
]
