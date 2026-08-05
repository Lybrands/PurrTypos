from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ModelRequest,
    ToolDataContract,
    ToolHandlerResult,
    ToolPolicy,
    ToolSchema,
)
from agent_core.errors import ContractViolationError
from agent_core.ports import ToolCatalog, ToolRegistration
from agent_core.tools import (
    model_visible_tool_schema,
    resolve_tool_display_name,
)
from agent_core.tools.contract import inspect_tool_contract
from agent_core.tools.registry import InMemoryToolCatalog


async def _handler(state, arguments, signal=None):
    return ToolHandlerResult("ok")


def _registration(
    name: str,
    *,
    handler=_handler,
    description: str = "Test tool",
    parameters=None,
    scope_validator=None,
    cache_probe=None,
    cancellation_linearizable=False,
    data_contract=ToolDataContract(),
):
    return ToolRegistration(
        schema=ToolSchema(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
        ),
        handler=handler,
        policy=ToolPolicy(mode="read", title=f"Use {name}"),
        scope_validator=scope_validator,
        cache_probe=cache_probe,
        cancellation_linearizable=cancellation_linearizable,
        data_contract=data_contract,
    )


def _request() -> AgentRunRequest:
    return AgentRunRequest(
        messages=(AgentMessage(role="user", content="test"),),
        model=ModelRequest(provider="test", model="model"),
        domain_context=DomainContext(namespace="test"),
        tools_enabled=True,
    )


def test_catalog_is_a_closed_instance_scoped_snapshot():
    source = [_registration("alpha")]
    first = InMemoryToolCatalog(source)
    source.append(_registration("late"))
    second = InMemoryToolCatalog((_registration("beta"),))

    assert isinstance(first, ToolCatalog)
    assert first.names == {"alpha"}
    assert first.enabled_names(_request()) == {"alpha"}
    assert tuple(schema.name for schema in first.schemas()) == ("alpha",)
    assert first.get("alpha") is first.registrations()[0]
    assert first.get("late") is None
    assert second.names == {"beta"}


def test_catalog_registration_schema_is_recursively_immutable_and_detached():
    parameters = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
    }
    catalog = InMemoryToolCatalog((_registration("alpha", parameters=parameters),))
    parameters["properties"]["value"]["type"] = "integer"

    schema = catalog.schemas()[0]
    assert schema.parameters["properties"]["value"]["type"] == "string"
    with pytest.raises(TypeError):
        schema.parameters["properties"]["value"]["type"] = "boolean"


def test_tool_schema_localizes_display_names_without_changing_protocol_name():
    schema = ToolSchema(
        name="readSource",
        description="Read source data.",
        parameters={"type": "object", "properties": {}},
        display_names={
            "zh_cn": "读取原作",
            "en-US": "Read Source",
        },
    )

    assert dict(schema.display_names) == {
        "zh-CN": "读取原作",
        "en-US": "Read Source",
    }
    assert resolve_tool_display_name(schema, "zh-Hans-CN") == "读取原作"
    assert resolve_tool_display_name(schema, "en-GB") == "Read Source"
    assert resolve_tool_display_name(schema, "invalid locale!") == "读取原作"
    localized = model_visible_tool_schema(schema, "zh-CN")
    assert localized.name == "readSource"
    assert localized.parameters == schema.parameters
    assert "读取原作" in localized.description
    assert "readSource" in localized.description
    with pytest.raises(TypeError):
        schema.display_names["ja-JP"] = "原作を読む"


def test_tool_schema_rejects_empty_or_duplicate_localized_names():
    with pytest.raises(ValueError, match="must not be empty"):
        ToolSchema(
            name="readSource",
            description="Read source data.",
            parameters={"type": "object", "properties": {}},
            display_names={"zh-CN": ""},
        )
    with pytest.raises(ValueError, match="duplicate normalized"):
        ToolSchema(
            name="readSource",
            description="Read source data.",
            parameters={"type": "object", "properties": {}},
            display_names={"zh-CN": "读取原作", "zh_cn": "阅读原作"},
        )


@pytest.mark.parametrize(
    "registrations,match",
    [
        ((_registration("same"), _registration("same")), "duplicate"),
        ((_registration("blank", description=""),), "description"),
        (
            (_registration("array", parameters={"type": "array", "items": {}}),),
            "type must be object",
        ),
        ((_registration("sync", handler=lambda *_args: None),), "handler must be async"),
        (
            (_registration("bad-receipt", cancellation_linearizable="yes"),),
            "cancellation_linearizable must be boolean",
        ),
        (
            (
                ToolRegistration(
                    schema=ToolSchema(
                        name="unclassified",
                        description="No policy",
                        parameters={"type": "object", "properties": {}},
                    ),
                    handler=_handler,
                    policy=None,  # type: ignore[arg-type]
                ),
            ),
            "explicit tool policy",
        ),
    ],
)
def test_catalog_fails_closed_for_invalid_registration_contracts(registrations, match):
    report = inspect_tool_contract(registrations)
    assert not report.is_valid
    with pytest.raises(ContractViolationError, match=match):
        InMemoryToolCatalog(registrations)


def test_async_callable_scope_and_sync_cache_probe_are_valid():
    class Scope:
        async def __call__(self, state, arguments, signal=None):
            return None

    class Probe:
        def will_hit(self, state, arguments):
            return True

    catalog = InMemoryToolCatalog((
        _registration("alpha", scope_validator=Scope(), cache_probe=Probe()),
    ))

    assert catalog.names == {"alpha"}


def test_catalog_enforces_model_and_host_data_ownership_paths():
    parameters = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "hostId": {"type": "string"},
                    },
                },
            },
        },
    }
    registration = _registration(
        "audited",
        parameters=parameters,
        data_contract=ToolDataContract(
            model_owned_paths=("items[].text", "missing"),
            host_derived_paths=("items[].hostId",),
        ),
    )

    report = inspect_tool_contract((registration,))

    assert not report.is_valid
    assert any("model-owned path is absent" in item for item in report.violations)
    assert any("host-owned path is exposed" in item for item in report.violations)


def test_catalog_accepts_audited_schema_with_hidden_host_fields():
    registration = _registration(
        "audited",
        parameters={
            "type": "object",
            "properties": {"semanticDelta": {"type": "string"}},
        },
        data_contract=ToolDataContract(
            model_owned_paths=("semanticDelta",),
            host_bound_paths=("projectId",),
            host_derived_paths=("revision", "contentText"),
            payload_mode="delta",
        ),
    )

    catalog = InMemoryToolCatalog((registration,))

    assert catalog.get("audited").data_contract.payload_mode.value == "delta"


def test_audited_catalog_rejects_new_model_field_without_an_owner():
    registration = _registration(
        "audited",
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "forgotten": {"type": "string"},
                        },
                    },
                },
            },
        },
        data_contract=ToolDataContract(
            model_owned_paths=("items[].text",),
            host_derived_paths=("revision",),
        ),
    )

    report = inspect_tool_contract((registration,))

    assert not report.is_valid
    assert any(
        "model-visible path has no declared owner: items[].forgotten" in item
        for item in report.violations
    )


def test_request_enablement_must_be_a_registered_subset():
    catalog = InMemoryToolCatalog(
        (_registration("alpha"),),
        enablement=lambda _request: {"alpha", "unknown"},
    )

    with pytest.raises(ContractViolationError, match="unregistered"):
        catalog.enabled_names(_request())
    with pytest.raises(ContractViolationError, match="unregistered"):
        catalog.schemas({"unknown"})
