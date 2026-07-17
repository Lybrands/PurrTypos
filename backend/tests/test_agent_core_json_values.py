from __future__ import annotations

import json

import pytest

from agent_core.contracts import AgentMessage, DomainContext, MessageRole
from agent_core.json_values import (
    FrozenDict,
    FrozenList,
    freeze_json_value,
    thaw_json_value,
)


def test_boundary_values_are_detached_deeply_immutable_and_json_compatible():
    source = {"nested": [{"value": "original"}], "flags": [True, None]}
    context = DomainContext(namespace="test", payload=source)
    source["nested"][0]["value"] = "mutated"

    assert context.payload["nested"][0]["value"] == "original"
    with pytest.raises(TypeError):
        context.payload["nested"][0]["value"] = "forbidden"
    with pytest.raises(TypeError):
        context.payload["flags"].append(False)
    with pytest.raises(TypeError):
        dict.__setitem__(context.payload, "injected", True)
    with pytest.raises(TypeError):
        dict.__setitem__(context.payload["nested"][0], "value", "injected")
    with pytest.raises(TypeError):
        list.append(context.payload["flags"], False)

    assert isinstance(context.payload, FrozenDict)
    assert not isinstance(context.payload, dict)
    assert isinstance(context.payload["flags"], FrozenList)
    assert not isinstance(context.payload["flags"], list)
    assert json.loads(json.dumps(thaw_json_value(context.payload))) == {
        "nested": [{"value": "original"}],
        "flags": [True, None],
    }


def test_thaw_returns_independent_mutable_adapter_payload():
    frozen = freeze_json_value({"rows": [{"id": 1}]})
    thawed = thaw_json_value(frozen)
    thawed["rows"][0]["id"] = 2

    assert type(thawed) is dict
    assert type(thawed["rows"]) is list
    assert type(thawed["rows"][0]) is dict
    assert frozen["rows"][0]["id"] == 1
    assert thawed["rows"][0]["id"] == 2
    assert json.loads(json.dumps(thawed)) == {"rows": [{"id": 2}]}


def test_frozen_values_keep_mapping_sequence_conversion_and_equality():
    plain = {"rows": [{"id": 1}], "flags": [True, None]}
    frozen = freeze_json_value(plain)

    assert dict(frozen) == plain
    assert list(frozen["rows"]) == plain["rows"]
    assert frozen == plain
    assert plain == frozen
    assert frozen["rows"] == plain["rows"]
    assert plain["rows"] == frozen["rows"]


def test_frozen_values_can_be_shared_across_contracts_without_shared_mutability():
    shared = freeze_json_value({"rows": [{"id": 1}]})
    context = DomainContext(namespace="test", payload={"shared": shared})
    message = AgentMessage(role=MessageRole.USER, content={"shared": shared})

    assert context.payload["shared"] is shared
    assert message.content["shared"] is shared
    with pytest.raises(TypeError):
        dict.__setitem__(shared["rows"][0], "id", 2)
    with pytest.raises(TypeError):
        list.append(shared["rows"], {"id": 2})

    thawed_message = thaw_json_value(message.content)
    thawed_message["shared"]["rows"][0]["id"] = 3
    assert context.payload["shared"]["rows"][0]["id"] == 1


def test_boundary_rejects_non_json_values_cycles_and_non_finite_numbers():
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(TypeError, match="unsupported JSON"):
        AgentMessage(role=MessageRole.USER, content={"bad": object()})
    with pytest.raises(TypeError, match="keys"):
        freeze_json_value({1: "bad"})
    with pytest.raises(ValueError, match="cyclic"):
        freeze_json_value(cyclic)
    with pytest.raises(ValueError, match="finite"):
        freeze_json_value(float("nan"))
