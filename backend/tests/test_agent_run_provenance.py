from __future__ import annotations

from application.run_provenance import build_chat_run_provenance, digest_model_endpoint
from schemas.ai import ChatStreamRequest


def _request(
    *,
    api_key: str = "secret-one",
    endpoint: str = "https://provider.example/v1/",
    temperature: float = 0.2,
    authorization: str = "Bearer first",
) -> ChatStreamRequest:
    return ChatStreamRequest(
        messages=[{"role": "user", "content": "同一个写作请求"}],
        apiKey=api_key,
        baseURL=endpoint,
        apiProvider="OpenAI",
        options={
            "model": "writing-model",
            "temperature": temperature,
            "authorization": authorization,
        },
        sessionId=7,
        enableAgentTools=True,
        bookId="writing-book",
        chatAgentMode="agent",
        contextWindow="200k",
    )


def test_profile_digest_excludes_credentials_and_normalizes_endpoint():
    first = build_chat_run_provenance(_request())
    second = build_chat_run_provenance(
        _request(
            api_key="secret-two",
            endpoint="https://provider.example/v1",
            authorization="Bearer second",
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


def test_profile_digest_covers_non_secret_request_fields():
    original = build_chat_run_provenance(_request())
    changed = build_chat_run_provenance(_request(temperature=0.7))

    assert original.request_profile_digest != changed.request_profile_digest
    assert original.endpoint_digest == changed.endpoint_digest
    assert digest_model_endpoint("https://provider.example/v1/") == (
        digest_model_endpoint("https://provider.example/v1")
    )


def test_provenance_freezes_the_capabilities_used_at_run_creation():
    provenance = build_chat_run_provenance(_request())

    assert provenance.capability_snapshot["schemaVersion"] == 1
    assert provenance.capability_snapshot["profileId"] == "generic"
    assert provenance.capability_snapshot["contextWindowTokens"] == 200_000
    assert provenance.capability_snapshot["protocol"]["reasoningControl"] == (
        "unavailable"
    )
