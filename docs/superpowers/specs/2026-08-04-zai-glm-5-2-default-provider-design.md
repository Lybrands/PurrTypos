# Z.ai GLM-5.2 Default Provider Design

## Goal

Add Z.ai as the first built-in model provider and make GLM-5.2 the default model for users without a valid saved preference. All GLM-5.2 chat and title-generation traffic must use the official `zai-sdk` package and the mainland-China `ZhipuAiClient`. Users supply their own API keys; the application never bundles credentials.

## Scope

This change adds one built-in provider, one built-in model profile, one backend SDK adapter, and the routing and tests needed to use them. It does not refactor the existing OpenAI or Anthropic adapters, add other Z.ai models, or make a paid live request during verification.

## Product Behavior

- The built-in model catalog lists Z.ai / GLM-5.2 before the existing Moonshot, MiniMax, and MiMo entries.
- Startup migration materializes a stable `zai:glm-5.2` configuration without requiring an add-model action.
- Until the user enters an API key, the entry remains visible in settings but is excluded from usable model selectors.
- After the key is saved, GLM-5.2 is the first usable model and therefore the default when no valid per-book or screenplay preference exists.
- A previously saved valid model preference remains selected; adding GLM-5.2 does not override explicit user choice.
- The API key is shared across built-in profiles belonging to the same Z.ai provider if more profiles are added later.

## Model Metadata

The GLM-5.2 built-in profile uses:

- profile ID: `zai:glm-5.2`
- provider ID: `zai`
- API provider: `zai`
- model name: `glm-5.2`
- client: `from zai import ZhipuAiClient`
- mainland endpoint: `https://open.bigmodel.cn/api/paas/v4/`
- default and maximum context choice: `1m`
- default Thinking state: enabled, but user-disableable
- default temperature: `1.0` for Thinking and non-Thinking calls
- maximum output capability: 128K tokens; individual application calls may continue to request lower limits

The profile sends the SDK-native `thinking={"type": "enabled" | "disabled"}` shape. It does not depend on OpenAI `extra_body` behavior.

## Architecture

### Frontend catalog

A new GLM-5.2 profile joins the existing TypeScript model registry at index zero. The `AiBuiltinProviderId` and API-provider unions expand to include `zai`. Existing migration machinery creates the stable built-in configuration, preserves matching legacy user data, and orders the Z.ai profile first.

All request-building paths must preserve `apiProvider: "zai"` instead of collapsing every non-Anthropic provider to `openai`. This includes the main AI panel, screenplay agent, editor actions, title generation, and any shared service request types.

### Backend routing

`provider_router` gains an explicit `zai` branch for streaming and non-streaming chat. The `/ai/title` route uses the same Z.ai adapter when `apiProvider` is `zai`. The models-list route lists models through the public `zai-sdk` models API and never falls back to the OpenAI SDK. The settings UI does not depend on model listing for built-in profiles.

### Z.ai adapter

`backend/infrastructure/models/zai_chat.py` owns all `zai-sdk` details:

- construct `ZhipuAiClient` with the user API key and normalized mainland endpoint;
- translate existing provider options into SDK keyword arguments;
- call `client.chat.completions.create` for ordinary and streaming requests;
- convert SDK response objects with their public serialization API into the OpenAI-shaped dictionaries already consumed by `ProviderModelGateway`;
- generate session titles through the same SDK;
- close SDK response/client resources on success, cancellation, and failure.

The official examples expose a synchronous iterator. Blocking creation and iteration therefore run through `asyncio.to_thread`, one iterator step at a time, so the FastAPI event loop remains responsive. The adapter keeps this thread bridge private; Agent Core continues to use its existing asynchronous, provider-neutral gateway.

## Request and Response Flow

1. Startup migration creates the Z.ai built-in entry and puts it first.
2. The user supplies an API key in settings.
3. A frontend request sends `apiProvider: "zai"`, `model: "glm-5.2"`, the endpoint, and current Thinking, temperature, token, and tool options.
4. The backend router selects `zai_chat.py`.
5. The adapter invokes `ZhipuAiClient.chat.completions.create`.
6. Non-stream responses become `{message, model, usage}`.
7. Streaming chunks become the existing `{choices, usage}` shape, including `content`, `reasoning_content`, `tool_calls`, `finish_reason`, and final usage when supplied.
8. `ProviderModelGateway` performs the existing provider-neutral normalization and emits Agent Core chunks.

## Tool Calling

The existing function-tool schema is forwarded through the SDK `tools` argument. `tool_choice` is forwarded only when present. Streaming tool-call fragments retain their SDK-provided index, ID, function name, and arguments so the existing Agent Core accumulator can assemble calls without Z.ai-specific logic.

If GLM-5.2 rejects `tool_choice="required"`, the existing unsupported-required-tool-choice compatibility path remains authoritative. No provider-specific retry silently weakens a required tool contract.

## Error Handling and Cancellation

- Empty API keys and missing endpoints fail before making a provider request.
- Authentication, rate-limit, timeout, validation, and upstream service errors retain the SDK exception text and are wrapped by the existing model gateway boundary.
- A stream checks the cancellation signal between SDK iterator steps and stops promptly after the current blocking step returns.
- Iterator, response, and client resources are closed in `finally` paths. Cleanup supports either synchronous or asynchronous close methods exposed by the installed SDK version.
- A malformed usage-only tail or empty-choice chunk cannot erase already completed content.
- The adapter does not log API keys or full user prompts.

## Compatibility and Migration

Existing OpenAI, Anthropic, Moonshot, MiniMax, MiMo, and custom configurations keep their behavior. A valid saved model preference remains valid. Known GLM-5.2 configurations are migrated only when both model name and normalized official endpoint match; proxy-hosted custom configurations are left custom.

The new dependency is added to `backend/requirements.txt` as `zai-sdk>=0.2.3`, the version documented by the current GLM-5.2 integration guide. Packaging configuration must include `zai` and its runtime dependencies in Electron builds.

## Test Strategy

Implementation follows red-green-refactor. Tests are added before production changes and must first fail for the missing Z.ai behavior.

Frontend tests cover:

- provider and preset uniqueness;
- GLM-5.2 metadata and first position;
- built-in materialization and exact legacy migration;
- default selection with no valid saved preference;
- preservation of a valid saved preference;
- request construction preserving `apiProvider: "zai"`.

Backend tests use fake SDK objects and cover:

- explicit provider routing for stream and non-stream calls;
- SDK-native request parameters for Thinking on and off;
- ordinary response, reasoning, usage, and tool-call normalization;
- streaming content, reasoning, tool-call fragments, finish reason, and usage;
- cancellation and resource cleanup;
- title generation through the Z.ai adapter;
- model-profile resolution and GLM-5.2 metadata;
- errors reaching the existing gateway boundary without credential leakage.

Verification runs focused Node tests, focused Python tests, the TypeScript build, and the broader relevant test suites. It does not make a live provider call or require a real API key.

## Success Criteria

- A fresh installation visibly includes Z.ai / GLM-5.2 as the first built-in model.
- After a user enters a valid key, GLM-5.2 becomes the default in the absence of an explicit valid preference.
- Main chat, Agent tool calls, streaming Thinking output, and title generation use `zai-sdk` through `ZhipuAiClient`.
- Existing providers and saved valid model preferences continue to work.
- Automated tests and the production build pass without a live API request.
