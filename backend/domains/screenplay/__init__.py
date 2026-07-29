"""Screenplay-domain adapters for the shared Agent Core."""

from domains.screenplay.adapter import ScreenplayDomainAdapter
from domains.screenplay.contracts import (
    SCREENPLAY_DOMAIN_NAMESPACE,
    ScreenplayDomainContext,
)

__all__ = [
    "SCREENPLAY_DOMAIN_NAMESPACE",
    "ScreenplayDomainAdapter",
    "ScreenplayDomainContext",
]
