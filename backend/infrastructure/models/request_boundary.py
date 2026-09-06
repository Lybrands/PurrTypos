"""The final SDK send boundary, shared by native and compatible transports."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

from purra.json_values import freeze_json_mapping, thaw_json_mapping
from purra.errors import ContractViolationError
from infrastructure.models.profiles.descriptors import DESCRIPTOR_KEY, TRACE_KEY
from infrastructure.models.profiles.descriptors import profile_for_options
from infrastructure.models.capabilities import normalize_thinking_enabled


@dataclass(frozen=True)
class SendContext:
    attempt_id: str
    options: Mapping
    provider: str
    generation_limit: int | None
    observer: object = None
    records: list = field(default_factory=list, compare=False)


_PENDING: ContextVar[tuple | None] = ContextVar("model_pending_send", default=None)
_ACTIVE: ContextVar[SendContext | None] = ContextVar("model_active_send", default=None)


def describe_attempt(gateway, invocation) -> str:
    attempt_id = "sdk-" + uuid4().hex
    _PENDING.set((id(gateway), id(invocation), attempt_id))
    return attempt_id


def managed_send(method):
    @wraps(method)
    async def wrapped(self, messages, invocation, signal=None):
        pending = _PENDING.get()
        attempt_id = pending[2] if pending and pending[:2] == (id(self), id(invocation)) else "sdk-" + uuid4().hex
        _PENDING.set(None)
        context = SendContext(attempt_id, invocation.request.options, invocation.request.provider,
                              invocation.max_generation_tokens, getattr(self, "_request_observer", None))
        token = _ACTIVE.set(context)
        try:
            return await method(self, messages, invocation, signal)
        finally:
            _ACTIVE.reset(token)
    return wrapped


@dataclass(frozen=True)
class PreparedProviderRequest:
    sdk_kwargs: Mapping
    record: Mapping


def prepare_request(params: Mapping, context: SendContext, protocol: str) -> PreparedProviderRequest:
    body = {**dict(params), **dict(params.get("extra_body") or {})}
    options = context.options
    effective = {}
    dispositions = {}
    for key in ("temperature", "reasoning_effort", "top_p", "top_k", "response_format", "thinking", "output_config"):
        requested = options.get(key)
        sent = body.get(key)
        if key == "thinking" and protocol == "openai_native":
            enabled = normalize_thinking_enabled(options)
            if (isinstance(requested, Mapping) and "budget_tokens" in requested
                    or enabled is False and body.get("reasoning_effort") != "none"
                    or enabled is True and body.get("reasoning_effort") == "none"):
                raise ContractViolationError("native reasoning control conflicts with request", code="model_parameter_not_forwarded")
            dispositions[key] = "mapped_to_native_reasoning_control"
            continue
        if requested is not None and sent != requested:
            # Anthropic adaptive/native reasoning is an explicit frozen protocol rule.
            mapped = False
            if key == "thinking":
                profile = profile_for_options(options)
                expected = profile.build_openai_extra_body(normalize_thinking_enabled(options))
                mapped = bool(expected) and all(body.get(k) == v for k, v in expected.items())
                if protocol.startswith("anthropic") and profile.native_anthropic_thinking:
                    mapped = normalize_thinking_enabled(options) is True
                if isinstance(requested, Mapping) and "budget_tokens" in requested:
                    mapped = False
            if not mapped:
                raise ContractViolationError(f"SDK parameter was omitted or changed: {key}", code="model_parameter_not_forwarded")
            dispositions[key] = "mapped_to_native_reasoning_control"
        else:
            dispositions[key] = "sent" if sent is not None else "intentionally_omitted"
        if sent is not None and key not in {"response_format"}:
            effective[key] = sent
    actual_limit = body.get("max_completion_tokens", body.get("max_tokens"))
    if context.generation_limit is not None and actual_limit != context.generation_limit:
        raise ContractViolationError("SDK generation limit differs from invocation allowance", code="model_generation_limit_conflict")
    effective.update(model=body.get("model"), maxGenerationTokens=actual_limit,
                     stream=body.get("stream", protocol == "anthropic_stream"))
    frozen = freeze_json_mapping(dict(params))
    digest = sha256(json.dumps(thaw_json_mapping(frozen), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    record = {"attemptId": context.attempt_id, "stage": "sdk_prepared", "protocol": protocol,
              "requestDigest": digest, "resolved": thaw_json_mapping(options.get(TRACE_KEY) or {}),
              "sent": effective, "parameterDispositions": dispositions}
    return PreparedProviderRequest(frozen, freeze_json_mapping(record))


async def prepare_sdk_request(params, options=None, *, protocol, context=None):
    context = context or _ACTIVE.get()
    if context is None:
        context = SendContext("sdk-" + uuid4().hex, options or {}, protocol,
                              (options or {}).get("max_tokens"))
    if context.records:
        raise ContractViolationError("SDK request repeated within one invocation attempt", code="model_unmanaged_retry")
    prepared = prepare_request(params, context, protocol)
    context.records.append(thaw_json_mapping(prepared.record))
    if context.observer is not None:
        await context.observer(thaw_json_mapping(prepared.record))
    return thaw_json_mapping(prepared.sdk_kwargs)


async def record_provider_response(context=None):
    context = context or _ACTIVE.get()
    if context is not None and context.records and context.observer is not None:
        await context.observer({**context.records[-1], "stage": "provider_response_received"})


class CheckedClient:
    """Public injected-client protocol used by published PurrA adapters."""

    def __init__(self, client, *, protocol, context=None):
        self._client = client
        self._protocol = protocol
        self._context = context or _ACTIVE.get()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create_chat))
        self.messages = SimpleNamespace(create=self._create_message, stream=self._stream_message)

    def with_options(self, **options):
        return CheckedClient(self._client.with_options(**options), protocol=self._protocol, context=self._context)

    async def close(self):
        await self._client.close()

    async def _create_chat(self, **params):
        checked = await prepare_sdk_request(params, protocol="openai_native", context=self._context)
        result = await self._client.chat.completions.create(**checked)
        await record_provider_response(self._context)
        return result

    async def _create_message(self, **params):
        checked = await prepare_sdk_request(params, protocol="anthropic_native", context=self._context)
        result = await self._client.messages.create(**checked)
        await record_provider_response(self._context)
        return result

    def _stream_message(self, **params):
        return _CheckedMessageStream(self._client, params, self._context)


class _CheckedMessageStream:
    def __init__(self, client, params, context):
        self._client, self._params, self._context = client, params, context
        self._manager = None

    async def __aenter__(self):
        checked = await prepare_sdk_request(self._params, protocol="anthropic_stream", context=self._context)
        self._manager = self._client.messages.stream(**checked)
        result = await self._manager.__aenter__()
        try:
            await record_provider_response(self._context)
        except BaseException:
            import sys
            await self._manager.__aexit__(*sys.exc_info())
            raise
        return result

    async def __aexit__(self, *args):
        return await self._manager.__aexit__(*args)
