from __future__ import annotations

import pytest

from agents.shared.implementation import (
    AgentImplementationIdentity,
    AgentKind,
    LEGACY_IMPLEMENTATION_ID,
    REPLACEMENT_IMPLEMENTATION_ID,
    replacement_implementation,
    resolve_implementation,
)


@pytest.mark.parametrize("agent_kind", tuple(AgentKind))
def test_replacement_identity_round_trips(agent_kind: AgentKind) -> None:
    identity = replacement_implementation(
        agent_kind,
        recipe_version=(2 if agent_kind is not AgentKind.WRITING else None),
    )

    assert identity.implementation_id == REPLACEMENT_IMPLEMENTATION_ID
    assert AgentImplementationIdentity.from_mapping(identity.to_mapping()) == identity


@pytest.mark.parametrize("agent_kind", tuple(AgentKind))
def test_missing_persisted_identity_is_explicitly_legacy(
    agent_kind: AgentKind,
) -> None:
    identity = resolve_implementation(None, expected_agent_kind=agent_kind)

    assert identity.agent_kind is agent_kind
    assert identity.implementation_id == LEGACY_IMPLEMENTATION_ID


def test_identity_cannot_be_resolved_for_another_agent() -> None:
    persisted = replacement_implementation(AgentKind.WRITING).to_mapping()

    with pytest.raises(ValueError, match="another Agent"):
        resolve_implementation(
            persisted,
            expected_agent_kind=AgentKind.SCREENPLAY,
        )


@pytest.mark.parametrize(
    "value",
    (
        {},
        {
            "agentKind": "writing",
            "implementationId": "purra-native",
            "implementationVersion": 1,
            "toolContractVersion": 1,
            "artifactSchemaVersion": 1,
            "unexpected": True,
        },
        {
            "agentKind": "writing",
            "implementationId": "Purra Native",
            "implementationVersion": 1,
            "toolContractVersion": 1,
            "artifactSchemaVersion": 1,
        },
        {
            "agentKind": "writing",
            "implementationId": "purra-native",
            "implementationVersion": 0,
            "toolContractVersion": 1,
            "artifactSchemaVersion": 1,
        },
        {
            "agentKind": "writing",
            "implementationId": "purra-native",
            "implementationVersion": 1.5,
            "toolContractVersion": 1,
            "artifactSchemaVersion": 1,
        },
        {
            "agentKind": "writing",
            "implementationId": "purra-native",
            "implementationVersion": 1,
            "toolContractVersion": "1",
            "artifactSchemaVersion": 1,
        },
    ),
)
def test_invalid_identity_fails_closed(value) -> None:
    with pytest.raises((ValueError, KeyError)):
        AgentImplementationIdentity.from_mapping(value)
