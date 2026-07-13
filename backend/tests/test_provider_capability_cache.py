from __future__ import annotations

from services.provider_capability_cache import (
    clear_provider_capability_cache,
    mark_required_tool_choice_unsupported,
    provider_capability_key,
    required_tool_choice_is_unsupported,
)


def test_required_tool_choice_negative_capability_is_scoped_to_provider_model_and_mode():
    clear_provider_capability_cache()
    key = provider_capability_key(
        api_provider="openai",
        base_url="https://example.test/v1/",
        model="Model-A",
        thinking_enabled=True,
    )
    other = provider_capability_key(
        api_provider="openai",
        base_url="https://example.test/v1",
        model="Model-A",
        thinking_enabled=False,
    )

    assert required_tool_choice_is_unsupported(key) is False
    mark_required_tool_choice_unsupported(key)
    assert required_tool_choice_is_unsupported(key) is True
    assert required_tool_choice_is_unsupported(other) is False
    clear_provider_capability_cache()
