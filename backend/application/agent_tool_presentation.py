"""Shared public-operation presentation contract for every Agent tool."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from purra.contracts import AgentRunRequest
from purra.json_values import thaw_json_mapping
from purra.ports import ToolCatalog, ToolRegistration
from purra.tools import InMemoryToolCatalog


PUBLIC_TOOL_LABEL_LOCALE = "zh-CN"
MAX_PUBLIC_TOOL_LABEL_CHARACTERS = 240


class AgentToolPresentationContractError(ValueError):
    """Raised when a tool cannot produce a safe canonical public label."""


def validate_agent_tool_catalog_presentation(
    profile_id: str,
    catalog: ToolCatalog,
) -> None:
    """Fail startup when a Profile leaves public tool operations ambiguous."""

    normalized_profile = str(profile_id or "").strip() or "<unknown>"
    violations: list[str] = []
    for registration in catalog.registrations():
        tool_name = str(registration.schema.name or "").strip() or "<unknown>"
        display_names = registration.schema.display_names
        fallback = (
            display_names.get(PUBLIC_TOOL_LABEL_LOCALE)
            if isinstance(display_names, Mapping)
            else None
        )
        if not isinstance(fallback, str) or not fallback.strip():
            violations.append(
                f"{tool_name}: missing schema.display_names"
                f"[{PUBLIC_TOOL_LABEL_LOCALE!r}]"
            )
        elif not _valid_public_label(fallback):
            violations.append(f"{tool_name}: invalid static public label")
        if registration.operation_display_params is None:
            violations.append(f"{tool_name}: missing operation_display_params")
    if violations:
        raise AgentToolPresentationContractError(
            f"Agent profile {normalized_profile!r} violates the public tool "
            "operation presentation contract: " + "; ".join(violations)
        )


def enforce_agent_tool_catalog_presentation(
    profile_id: str,
    catalog: ToolCatalog,
) -> ToolCatalog:
    """Guard every emitted tool Operation, including request-scoped extras."""

    validate_agent_tool_catalog_presentation(profile_id, catalog)
    registrations = tuple(
        _guard_registration(profile_id, registration)
        for registration in catalog.registrations()
    )
    available_names = frozenset(item.schema.name for item in registrations)

    def enabled_names(request: AgentRunRequest) -> set[str]:
        return set(catalog.enabled_names(request)) & available_names

    return InMemoryToolCatalog(registrations, enablement=enabled_names)


def _guard_registration(
    profile_id: str,
    registration: ToolRegistration,
) -> ToolRegistration:
    resolver = registration.operation_display_params
    if resolver is None:  # The startup validator already reports the tool name.
        raise AgentToolPresentationContractError(
            "tool operation display resolver is required"
        )
    tool_name = str(registration.schema.name or "").strip() or "<unknown>"

    def resolve(state, arguments, tool_call) -> Mapping[str, Any]:
        # PurrA deliberately freezes model arguments and nested host state.
        # Presentation belongs to the host adapter boundary just like a domain
        # handler, so resolvers must receive ordinary JSON containers.  Without
        # this normalization arrays such as chapterIds, partKeys, sceneIds, and
        # roles silently fall back to a generic tool label.
        state_domain = getattr(state, "domain", None)
        projected_state = (
            replace(state, domain=thaw_json_mapping(state_domain))
            if isinstance(state_domain, Mapping)
            else state
        )
        projected = resolver(
            projected_state,
            thaw_json_mapping(arguments),
            tool_call,
        )
        if not isinstance(projected, Mapping):
            raise AgentToolPresentationContractError(
                _projection_error(profile_id, tool_name, "must be a mapping")
            )
        display_names = projected.get("displayNames")
        if not isinstance(display_names, Mapping):
            raise AgentToolPresentationContractError(
                _projection_error(
                    profile_id,
                    tool_name,
                    "must contain displayNames",
                )
            )
        label = display_names.get(PUBLIC_TOOL_LABEL_LOCALE)
        if not isinstance(label, str) or not _valid_public_label(label):
            raise AgentToolPresentationContractError(
                _projection_error(
                    profile_id,
                    tool_name,
                    f"must contain a single-line {PUBLIC_TOOL_LABEL_LOCALE} "
                    f"label of at most {MAX_PUBLIC_TOOL_LABEL_CHARACTERS} "
                    "characters",
                )
            )
        return projected

    return replace(registration, operation_display_params=resolve)


def _valid_public_label(value: str) -> bool:
    return bool(
        value.strip()
        and "\n" not in value
        and "\r" not in value
        and len(value) <= MAX_PUBLIC_TOOL_LABEL_CHARACTERS
    )


def _projection_error(profile_id: str, tool_name: str, detail: str) -> str:
    normalized_profile = str(profile_id or "").strip() or "<unknown>"
    return (
        f"Agent profile {normalized_profile!r} tool {tool_name!r} public "
        f"operation projection {detail}"
    )


__all__ = [
    "AgentToolPresentationContractError",
    "MAX_PUBLIC_TOOL_LABEL_CHARACTERS",
    "PUBLIC_TOOL_LABEL_LOCALE",
    "enforce_agent_tool_catalog_presentation",
    "validate_agent_tool_catalog_presentation",
]
