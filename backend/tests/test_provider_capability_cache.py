from __future__ import annotations

from infrastructure.models.provider_capabilities import ProviderCapabilityCache


def test_provider_capability_instances_do_not_share_observations():
    first = ProviderCapabilityCache()
    second = ProviderCapabilityCache()
    key = first.key(
        api_provider="OpenAI",
        base_url="https://example.test/v1/",
        model="Model-A",
        thinking_enabled=False,
    )

    first.mark_required_tool_choice_unsupported(key)

    assert first.required_tool_choice_is_unsupported(key) is True
    assert second.required_tool_choice_is_unsupported(key) is False
    first.clear()
    assert first.required_tool_choice_is_unsupported(key) is False
