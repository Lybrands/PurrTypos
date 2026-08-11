"""Stable host-facing entry points for complete Agent runs.

Product code may register contracts and adapters from the narrower public
packages, but complete execution is entered through this module.  Runtime
implementation modules are not application entry points.
"""

from purra.engine import AgentCore, AgentCoreRunOptions, CoreRunUpdate
from purra.execution import AgentRunHandle

__all__ = [
    "AgentCore",
    "AgentCoreRunOptions",
    "AgentRunHandle",
    "CoreRunUpdate",
]
