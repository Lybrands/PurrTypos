from __future__ import annotations

from types import SimpleNamespace

import pytest

from purra.contracts import ToolExecutionMode, ToolPolicy, ToolSchema
from purra.json_values import freeze_json_mapping
from purra.ports import ToolRegistration
from purra.tools import InMemoryToolCatalog
from application.agent_composition import _compose_tool_catalog
from application.agent_profile_registry import (
    AgentProfileRegistry,
    StaticAgentProfile,
)
from application.agent_tool_presentation import (
    AgentToolPresentationContractError,
    enforce_agent_tool_catalog_presentation,
)


async def _unused_handler(state, arguments, signal=None):
    del state, arguments, signal
    raise AssertionError("presentation contract tests do not execute tools")


def _registration(
    *,
    name="readDemo",
    display_names=None,
    resolver=None,
) -> ToolRegistration:
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description="Read one demo target.",
            parameters={"type": "object", "properties": {}},
            display_names=(
                {"zh-CN": "读取演示"}
                if display_names is None
                else display_names
            ),
        ),
        handler=_unused_handler,
        policy=ToolPolicy(ToolExecutionMode.READ, "读取演示"),
        operation_display_params=resolver,
    )


def _profile(registration: ToolRegistration) -> StaticAgentProfile:
    adapter = SimpleNamespace(
        tool_catalog=InMemoryToolCatalog((registration,)),
    )
    return StaticAgentProfile(
        id="demo",
        domain_namespace="test.demo",
        adapter=adapter,
    )


def test_profile_registry_rejects_tool_without_dynamic_public_projection():
    with pytest.raises(
        AgentToolPresentationContractError,
        match="readDemo: missing operation_display_params",
    ):
        AgentProfileRegistry((_profile(_registration()),))


def test_request_scoped_extra_tool_cannot_bypass_public_projection_contract():
    base = _registration(
        resolver=lambda _state, _arguments, _call: {
            "displayNames": {"zh-CN": "读取演示：第一章"}
        },
    )

    with pytest.raises(
        AgentToolPresentationContractError,
        match="readExtra: missing operation_display_params",
    ):
        _compose_tool_catalog(
            InMemoryToolCatalog((base,)),
            profile_id="demo",
            extras=(_registration(name="readExtra"),),
        )


def test_profile_registry_rejects_tool_without_localized_fallback():
    registration = _registration(
        display_names={},
        resolver=lambda _state, _arguments, _call: {
            "displayNames": {"zh-CN": "读取演示：第一章"}
        },
    )

    with pytest.raises(
        AgentToolPresentationContractError,
        match="missing schema.display_names",
    ):
        AgentProfileRegistry((_profile(registration),))


@pytest.mark.parametrize(
    "projection",
    (
        {},
        {"displayNames": {}},
        {"displayNames": {"zh-CN": "读取演示\n泄漏正文"}},
        {"displayNames": {"zh-CN": "读" * 241}},
    ),
)
def test_runtime_guard_rejects_invalid_public_projection(projection):
    registration = _registration(
        resolver=lambda _state, _arguments, _call: projection,
    )
    catalog = enforce_agent_tool_catalog_presentation(
        "demo",
        InMemoryToolCatalog((registration,)),
    )
    guarded = catalog.registrations()[0]

    with pytest.raises(AgentToolPresentationContractError):
        guarded.operation_display_params(
            SimpleNamespace(),
            {},
            SimpleNamespace(name="readDemo"),
        )


def test_runtime_guard_preserves_valid_business_projection():
    expected = {"displayNames": {"zh-CN": "读取演示：第一章"}}
    registration = _registration(
        resolver=lambda _state, _arguments, _call: expected,
    )
    catalog = enforce_agent_tool_catalog_presentation(
        "demo",
        InMemoryToolCatalog((registration,)),
    )

    actual = catalog.registrations()[0].operation_display_params(
        SimpleNamespace(),
        {},
        SimpleNamespace(name="readDemo"),
    )

    assert actual == expected


def test_runtime_guard_thaws_model_arguments_before_business_projection():
    seen = None

    def resolver(_state, arguments, _call):
        nonlocal seen
        seen = arguments
        targets = arguments.get("partKeys")
        assert isinstance(targets, list)
        return {
            "displayNames": {
                "zh-CN": f"读取{targets[0]}对应的已完成产出",
            }
        }

    catalog = enforce_agent_tool_catalog_presentation(
        "demo",
        InMemoryToolCatalog((_registration(resolver=resolver),)),
    )

    actual = catalog.registrations()[0].operation_display_params(
        SimpleNamespace(),
        freeze_json_mapping({"partKeys": ["第 1 集第 2 场"]}),
        SimpleNamespace(name="readDemo"),
    )

    assert isinstance(seen, dict)
    assert actual["displayNames"]["zh-CN"] == "读取第 1 集第 2 场对应的已完成产出"
