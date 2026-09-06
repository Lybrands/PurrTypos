from __future__ import annotations

from application.run_provenance import build_chat_run_provenance, digest_model_endpoint
from application.request_mapping import to_writing_agent_request
from schemas.ai import ChatStreamRequest


def _request(
    *,
    api_key: str = "secret-one",
    endpoint: str = "https://provider.example/v1/",
    temperature: float = 0.2,
) -> ChatStreamRequest:
    return ChatStreamRequest(
        messages=[{"role": "user", "content": "同一个写作请求"}],
        apiKey=api_key,
        baseURL=endpoint,
        apiProvider="OpenAI",
        options={
            "model": "writing-model",
            "temperature": temperature,
            "profile_max_generation_tokens": 120_000,
            "max_generation_tokens": 80_000,
            "supports_thinking": False,
            "thinking_only": False,
        },
        sessionId=7,
        enableAgentTools=True,
        bookId="writing-book",
        chatAgentMode="agent",
        contextWindow="200k",
    )


def _provenance(
    body: ChatStreamRequest,
    *,
    result_capacity_target_tokens: int | None = None,
):
    provider_options = {
        **dict(body.options or {}),
        "baseURL": body.baseURL or "",
    }
    request = to_writing_agent_request(body, provider_options)
    return build_chat_run_provenance(
        body,
        request.model,
        result_capacity_target_tokens=result_capacity_target_tokens,
    )


def test_profile_digest_excludes_credentials_and_normalizes_endpoint():
    first = _provenance(_request())
    second = _provenance(
        _request(
            api_key="secret-two",
            endpoint="https://provider.example/v1",
        ),
    )

    assert first.model_provider == second.model_provider == "openai"
    assert first.model_name == second.model_name == "writing-model"
    assert first.context_window == second.context_window == 200_000
    assert first.endpoint_digest == second.endpoint_digest
    assert first.request_profile_digest == second.request_profile_digest
    assert first.capability_snapshot == second.capability_snapshot
    assert first.execution_intent is not None
    assert first.execution_intent.capability_snapshot_digest == (
        first.capability_snapshot["digest"]
    )
    assert first.execution_intent.requested_user_max_generation_tokens == 80_000
    assert first.execution_intent.result_capacity_target_tokens is None


def test_profile_digest_covers_non_secret_request_fields():
    original = _provenance(_request())
    changed = _provenance(_request(temperature=0.7))

    assert original.request_profile_digest != changed.request_profile_digest
    assert original.endpoint_digest == changed.endpoint_digest
    assert digest_model_endpoint("https://provider.example/v1/") == (
        digest_model_endpoint("https://provider.example/v1")
    )


def test_provenance_freezes_the_capabilities_used_at_run_creation():
    provenance = _provenance(_request())

    assert provenance.capability_snapshot["schemaVersion"] == 2
    assert provenance.capability_snapshot["profileId"] == "generic"
    assert provenance.capability_snapshot["contextWindowTokens"] == 200_000
    assert provenance.capability_snapshot["maxGenerationTokens"] == 120_000
    assert provenance.capability_snapshot["protocol"]["reasoningControl"] == (
        "unavailable"
    )
    assert provenance.execution_intent is not None


def test_provenance_fences_user_ceiling_and_result_capacity_target():
    without_target = _provenance(_request())
    with_target = _provenance(
        _request(),
        result_capacity_target_tokens=16_384,
    )

    assert without_target.request_profile_digest != with_target.request_profile_digest
    assert with_target.execution_intent is not None
    assert with_target.execution_intent.requested_user_max_generation_tokens == 80_000
    assert with_target.execution_intent.result_capacity_target_tokens == 16_384
