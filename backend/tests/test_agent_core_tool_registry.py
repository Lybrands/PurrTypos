from __future__ import annotations

import pytest

from agent_core.contracts import (
    AgentMessage,
    AgentRunRequest,
    DomainContext,
    ModelRequest,
    ToolHandlerResult,
    ToolPolicy,
    ToolSchema,
)
from agent_core.errors import ContractViolationError
from agent_core.ports import ToolCatalog, ToolRegistration
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


def test_request_enablement_must_be_a_registered_subset():
    catalog = InMemoryToolCatalog(
        (_registration("alpha"),),
        enablement=lambda _request: {"alpha", "unknown"},
    )

    with pytest.raises(ContractViolationError, match="unregistered"):
        catalog.enabled_names(_request())
    with pytest.raises(ContractViolationError, match="unregistered"):
        catalog.schemas({"unknown"})
