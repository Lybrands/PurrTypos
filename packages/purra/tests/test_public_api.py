from __future__ import annotations

from typing import get_args


def test_complete_run_api_is_importable_from_the_package_boundary():
    from purra.api import (
        AgentCore,
        AgentCoreRunOptions,
        AgentRunHandle,
        CoreRunUpdate,
    )

    assert AgentCore.__module__.startswith("purra.")
    assert AgentCoreRunOptions.__module__.startswith("purra.")
    assert AgentRunHandle.__module__.startswith("purra.")
    assert {
        member.__module__ for member in get_args(CoreRunUpdate)
    } <= {
        "purra.events",
        "purra.contracts",
        "purra.output.contracts",
    }
