"""Business-agnostic, independently runnable Agent Core.

Use :class:`agent_core.engine.AgentCore` as the high-level event-stream entry
point. Applications and domains implement or register only the ports and
capabilities declared by this package; Core never imports them back.
"""
