from __future__ import annotations

def test_complete_run_api_is_importable_from_the_package_boundary():
    from purra.api import (
        AgentCore,
        AgentCoreRunOptions,
        AgentRunHandle,
    )

    assert AgentCore.__module__.startswith("purra.")
    assert AgentCoreRunOptions.__module__.startswith("purra.")
    assert AgentRunHandle.__module__.startswith("purra.")
    assert hasattr(AgentCore, "submit")
    assert not hasattr(AgentCore, "run")
