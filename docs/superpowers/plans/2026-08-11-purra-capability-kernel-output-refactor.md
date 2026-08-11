# PurrA Capability Kernel and Canonical Output Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 将 PurrA 重构为业务不可绕过的通用 Agent 能力内核，使模型调用、真实流式输出、Run 执行寿命、Operation 计时和子 Run 委派各有唯一权威所有者。

**Architecture:** AgentCore.submit() 是完整 Agent Run 的唯一入口；AgentModelInvocationManager 拦截全部 Provider 调用，AgentOutputProcessor 把 Provider chunk 与结构化生命周期事件先持久化再发布，AgentRunSupervisor 使执行生命与 SSE 订阅解耦。AgentOperationController 和 AgentDelegationCoordinator 分别拥有操作时序与父子 Run 语义；Writing/Screenplay 只注册通用端口并消费同一规范事件。

**Tech Stack:** Python 3.12, asyncio, dataclasses/Protocol, FastAPI SSE, SQLite/aiosqlite, React 18, TypeScript, Node test runner, pytest.

## Global Constraints

- 实施依据是 docs/superpowers/specs/2026-08-11-purra-agent-output-processor-design.md，它的不可绕过规则优先于旧 Runtime/Screenplay 兼容行为。
- 公开自然语言只能来自 Provider 的真实 content_delta；禁止宿主模板、结构化字段投影、事后拆字和前端逐字动画伪造流式。
- structured_private 与 reasoning_private 永不转公开；带全文 Validator/Judge 的调用不得使用 live。
- 数据库提交必须先于 SSE；实时、重连和刷新只消费同一份 canonical journal。
- SSE 断开不取消 Run；只有显式取消、持久化控制面命令或执行失效才能终止 Run。
- 业务生产代码不得直接 import purra.runtime、purra.run_controller 或具体 ModelGateway，不得存在任意 append_run_event 入口。
- PurrA 不得出现 screenplay、episode、scene、revision、novel 或 writing 分支。
- 新旧输出协议不双写，不设生产 fallback；迁移到新链路的同一任务内删除对应旧代码。
- 数据清理先生成功 dry-run 清单；真正删除数据必须单独获得用户确认，且整笔事务回滚安全。
- 现有工作树含用户未提交改动；执行时先用 superpowers:using-git-worktrees 或明确文件范围隔离，不覆盖、不顺带提交无关文件。
- Agent Core 架构改动执行 @ponytail off；以所有权、确定性、原子性、恢复和回归门禁为准，不以最小 diff 为目标。
- Python 直接测试使用 .venv/bin/python -m pytest；真实 Provider E2E 如因凭据缺失跳过，必须标记为 RELEASE BLOCKER。

---

## File Structure

### PurrA 通用能力

- Create packages/purra/src/purra/model_invocation/contracts.py: Invocation 意图、提交模式、不可变 receipt 和调用请求。
- Create packages/purra/src/purra/model_invocation/manager.py: Provider 唯一调用边界，统一能力、上限、终止、用量、证据和 chunk 观察。
- Create packages/purra/src/purra/output/contracts.py: output stream、canonical event、public fact bundle 和封闭结构化事件类型。
- Create packages/purra/src/purra/output/ports.py: output repository、publisher、policy 和 committed-result facts 端口。
- Create packages/purra/src/purra/output/processor.py: 唯一规范输出入口，先持久化再发布。
- Create packages/purra/src/purra/operations/contracts.py: model/tool/validation/compaction/delegation Operation 契约。
- Create packages/purra/src/purra/operations/controller.py: 单调时钟结算、壁钟记录和唯一终态。
- Create packages/purra/src/purra/execution/handle.py: AgentRunHandle 公开句柄协议。
- Create packages/purra/src/purra/execution/supervisor.py: 执行 task、lease、心跳、取消和订阅者解耦。
- Create packages/purra/src/purra/delegation/coordinator.py: 父子 Run 状态、结果聚合与 canonical event federation。
- Modify packages/purra/src/purra/api/__init__.py: 只暴露 AgentCore、AgentRunHandle 和稳定公开契约。
- Modify packages/purra/src/purra/engine/orchestrator.py: 组装五个通用所有者，执行两种响应事务。
- Modify packages/purra/src/purra/runtime/orchestrator.py: 删除 Gateway 直调与 Assistant 文本事件生成，只消费 manager 结果。
- Modify packages/purra/src/purra/tools/executor.py and context_orchestration/compaction.py: 把真实操作边界交给 Operation controller。
- Modify packages/purra/src/purra/ports/projection.py: Domain projector 只返回 typed effect，不再替换事件包络。

### 持久化、装配与业务适配

- Modify backend/database/schema.py: 扩展 ai_agent_run_events，新增 ai_agent_output_streams、序号/幂等约束和不可变触发器。
- Create backend/infrastructure/persistence/sqlite_agent_output_repository.py: 规范事件日志、stream 状态、Conversation 投影与原子提交。
- Modify backend/infrastructure/persistence/sqlite_run_repository.py: Run 终态与 canonical terminal event 原子绑定，不再接受业务任意事件。
- Modify backend/application/agent_composition.py: 只装配 Gateway/repository/publisher/policy/clock 等端口，由 AgentCore 创建通用所有者；删除 append_run_event()。
- Modify backend/application/agent_run_service.py: 使用 submit/subscribe/wait，删除父子 Run 手工多路复用与 HTTP 生命周期拥有。
- Modify backend/application/sse_mapping.py: 只序列化 AgentOutputEvent，不解释文字语义。
- Modify Screenplay output files: 改为通用契约适配后删除伪流式/模板输出代码。
- Modify Writing model call sites found by the architecture scan: 所有模型子调用迁入 manager，不引入业务特例。
- Create backend/database/crud/screenplay_agent_runtime_cleanup.py: 只读解析、dry-run 清单和单事务定向清理。

### 前端规范消费

- Create src/agent-runtime/canonicalOutput.ts: canonical event 解析、cursor 与唯一 reducer。
- Modify src/Workspace/AiPanel/hooks/chunkHandlers/*.ts and hooks/chat.types.ts: 实时/重放统一进入 canonical reducer，删除旧 chunk 猜测。
- Modify AssistantMessageBody.tsx: 只渲染 Provider 公开流和 committed final。
- Modify WorkLog/*, ToolCallStatus/*, SubAgentStatusList/*: 按 Operation 事件分组、计时、折叠，不根据本地到达时间推测。
- Modify AiPanel/index.tsx and ScreenplayAgentPage/index.tsx: 只订阅 canonical cursor，任务结束且最终输出 commit 后隐藏进度胶囊。

---

### Task 1: Ratchet the Architecture and Define Closed Contracts

**Files:**
- Create: packages/purra/tests/test_output_contracts.py
- Create: packages/purra/tests/test_operation_contracts.py
- Create: packages/purra/src/purra/output/__init__.py
- Create: packages/purra/src/purra/output/contracts.py
- Create: packages/purra/src/purra/operations/__init__.py
- Create: packages/purra/src/purra/operations/contracts.py

**Interfaces:**
- Consumes: existing RunId, ModelStreamChunk, ModelFinishReason and immutable dataclass conventions.
- Produces: AgentOutputIntent, OutputCommitMode, OutputSource, OutputChannel, OutputVisibility, OutputEventKind, OutputStreamSpec, AgentOutputEventDraft, AgentOutputEvent, RunLifecycleOutputDraft, ToolOutputEvent, DomainEffectOutput, OperationKind, OperationStatus, OperationStarted, OperationFinished.

- [x] **Step 1: Write fail-closed contract tests**

~~~python
def load_output_contracts():
    try:
        return importlib.import_module("purra.output.contracts")
    except ModuleNotFoundError as error:
        pytest.fail(f"canonical output contracts are missing: {error}")

def load_operation_contracts():
    try:
        return importlib.import_module("purra.operations.contracts")
    except ModuleNotFoundError as error:
        pytest.fail(f"operation contracts are missing: {error}")

def test_private_intent_cannot_use_live_commit_mode():
    contracts = load_output_contracts()
    with pytest.raises(ValueError, match="private intent cannot be live"):
        contracts.OutputStreamSpec(
            output_stream_id="out-1",
            run_id="run-1",
            turn_id="turn-1",
            invocation_id="inv-1",
            intent=contracts.AgentOutputIntent.STRUCTURED_PRIVATE,
            commit_mode=contracts.OutputCommitMode.LIVE,
        )

def test_public_text_draft_requires_provider_source():
    contracts = load_output_contracts()
    with pytest.raises(ValueError, match="public text requires provider source"):
        contracts.AgentOutputEventDraft.public_text(
            run_id="run-1",
            turn_id="turn-1",
            output_stream_id="out-1",
            invocation_id="inv-1",
            source_event_key="provider:1",
            source=contracts.OutputSource.RUNTIME,
            channel=contracts.OutputChannel.FINAL,
            delta="不应公开",
            occurred_at=datetime.now(timezone.utc),
        )

def test_finished_operation_rejects_negative_duration():
    contracts = load_operation_contracts()
    with pytest.raises(ValueError, match="duration must be non-negative"):
        contracts.OperationFinished(
            operation_id="op-1",
            run_id="run-1",
            status=contracts.OperationStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
            duration_ms=-1,
        )
~~~

- [x] **Step 2: Run the contract tests and verify red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_output_contracts.py packages/purra/tests/test_operation_contracts.py -q

Expected: test FAIL with “canonical output contracts are missing” and the equivalent Operation message. This is the missing-feature failure that Task 1 makes green; legacy-path boundary failures are intentionally deferred to Task 9, where those paths are removed.

- [x] **Step 3: Define the closed output and operation contracts**

~~~python
class AgentOutputIntent(StrEnum):
    EXECUTION_PUBLIC = "execution_public"
    FINAL_PUBLIC = "final_public"
    STRUCTURED_PRIVATE = "structured_private"
    REASONING_PRIVATE = "reasoning_private"

class OutputCommitMode(StrEnum):
    LIVE = "live"
    GATED = "gated"
    PRIVATE = "private"

@dataclass(frozen=True, slots=True)
class OutputStreamSpec:
    output_stream_id: str
    run_id: RunId
    turn_id: str | None
    invocation_id: str
    intent: AgentOutputIntent
    commit_mode: OutputCommitMode

@dataclass(frozen=True, slots=True)
class AgentOutputEvent:
    event_id: str
    output_stream_id: str | None
    run_id: RunId
    turn_id: str | None
    invocation_id: str | None
    sequence: int
    source: OutputSource
    kind: OutputEventKind
    channel: OutputChannel
    visibility: OutputVisibility
    payload: Mapping[str, JsonValue]
    occurred_at: datetime
    emitted_at: datetime

@dataclass(frozen=True, slots=True)
class AgentOutputEventDraft:
    run_id: RunId
    turn_id: str | None
    output_stream_id: str | None
    invocation_id: str | None
    source_event_key: str
    source: OutputSource
    kind: OutputEventKind
    channel: OutputChannel
    visibility: OutputVisibility
    payload: Mapping[str, JsonValue]
    occurred_at: datetime

@dataclass(frozen=True, slots=True)
class RunLifecycleOutputDraft:
    status: RunStatus
    payload: Mapping[str, JsonValue]
    occurred_at: datetime

@dataclass(frozen=True, slots=True)
class ToolOutputEvent:
    operation_id: str
    tool_call_id: str
    tool_name: str
    status: str
    occurred_at: datetime

@dataclass(frozen=True, slots=True)
class DomainEffectOutput:
    effect: DomainEffect
    occurred_at: datetime

class OperationKind(StrEnum):
    MODEL = "model"
    TOOL = "tool"
    VALIDATION = "validation"
    CONTEXT_COMPACTION = "context_compaction"
    DELEGATION = "delegation"
~~~

Contract validation rejects public visibility with private intent, live with structured_private/reasoning_private, arbitrary public payload keys and negative sequence/duration. Task 5 controller tests enforce exactly one terminal Operation for each start.

- [x] **Step 4: Run focused contract tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_output_contracts.py packages/purra/tests/test_operation_contracts.py -q

Expected: PASS for enum coercion, immutable field validation and every fail-closed combination.

- [x] **Step 5: Commit the contract ratchet**

~~~bash
git add packages/purra/src/purra/output packages/purra/src/purra/operations packages/purra/tests/test_output_contracts.py packages/purra/tests/test_operation_contracts.py
git commit -m "test(agent): ratchet canonical output ownership"
~~~

### Task 2: Make the Canonical Journal the Only Durable Output Truth

**Files:**
- Create: packages/purra/src/purra/output/ports.py
- Modify: packages/purra/src/purra/ports/__init__.py
- Modify: packages/purra/src/purra/ports/projection.py
- Modify: backend/database/schema.py
- Create: backend/infrastructure/persistence/sqlite_agent_output_repository.py
- Modify: backend/infrastructure/persistence/sqlite_run_repository.py
- Create: backend/tests/test_sqlite_agent_output_repository.py
- Modify: backend/tests/test_purra_sqlite_run_repository.py

**Interfaces:**
- Consumes: Task 1 OutputStreamSpec and AgentOutputEvent.
- Produces: AgentOutputRepository, AgentOutputPublisher, AgentOutputPolicy; SQLite atomic implementations of open_stream, append_event, commit_run_lifecycle, commit_stream, abort_stream and list_events. DomainEventProjector becomes a no-return typed effect projector.

- [ ] **Step 1: Write repository transaction and replay tests**

~~~python
async def test_append_allocates_turn_sequence_and_is_queryable_before_publish():
    await repository.open_stream(spec)
    event = await repository.append_event(draft)
    assert event.sequence == 1
    assert await repository.list_events(spec.run_id, after_sequence=0) == (event,)

async def test_duplicate_source_key_returns_existing_event():
    first = await repository.append_event(draft)
    second = await repository.append_event(draft)
    assert second.event_id == first.event_id
    assert second.sequence == first.sequence

async def test_commit_final_stream_projects_one_conversation_answer_atomically():
    event = await repository.commit_stream(stream_id, ModelFinishReason.STOP)
    assert event.kind is OutputEventKind.STREAM_COMMITTED
    assert await load_conversation_response(run_id) == "".join(parts)

async def test_run_terminal_and_canonical_event_commit_together():
    event = await repository.commit_run_lifecycle(run_id, run_commit, draft)
    assert await load_run_status(run_id) == "done"
    events = await repository.list_events(run_id, after_sequence=0)
    assert events[-1] == event

async def test_domain_projector_cannot_replace_event_envelope():
    assert await projector.project(run_id, domain_effect) is None
~~~

- [ ] **Step 2: Run repository tests to verify missing schema/port failures**

Run: .venv/bin/python -m pytest backend/tests/test_sqlite_agent_output_repository.py backend/tests/test_purra_sqlite_run_repository.py -q

Expected: FAIL because ai_agent_output_streams, canonical journal columns and the repository port do not exist.

- [ ] **Step 3: Extend the existing event journal**

~~~sql
CREATE TABLE IF NOT EXISTS ai_agent_output_streams (
    id TEXT PRIMARY KEY NOT NULL,
    run_id TEXT NOT NULL,
    turn_id TEXT DEFAULT NULL,
    invocation_id TEXT NOT NULL UNIQUE,
    intent TEXT NOT NULL,
    commit_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    finish_reason TEXT DEFAULT NULL,
    create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME DEFAULT CURRENT_TIMESTAMP
)
~~~

Extend ai_agent_run_events with event_id, turn_id, invocation_id, output_stream_id, sequence, source, kind, channel, visibility, occurred_at, emitted_at and source_event_key. Create a unique expression index on (COALESCE(turn_id, run_id), sequence), UNIQUE(source_event_key) and immutable-envelope triggers. Migrate existing lifecycle rows to source=runtime, structured lifecycle channel and stable sequence in original id order.

- [ ] **Step 4: Implement the repository port**

~~~python
@runtime_checkable
class AgentOutputRepository(Protocol):
    async def open_stream(self, spec: OutputStreamSpec) -> OutputStreamSpec: ...
    async def append_event(self, draft: AgentOutputEventDraft) -> AgentOutputEvent: ...
    async def commit_run_lifecycle(
        self,
        run_id: RunId,
        commit: RunCommit,
        draft: RunLifecycleOutputDraft,
    ) -> AgentOutputEvent: ...
    async def commit_stream(
        self, output_stream_id: str, finish_reason: ModelFinishReason
    ) -> AgentOutputEvent: ...
    async def abort_stream(
        self, output_stream_id: str, error_code: str
    ) -> AgentOutputEvent: ...
    async def list_events(
        self, run_id: RunId, *, after_sequence: int, limit: int = 200
    ) -> tuple[AgentOutputEvent, ...]: ...

@runtime_checkable
class AgentOutputPublisher(Protocol):
    async def publish_committed(self, event: AgentOutputEvent) -> None: ...
    async def wait_for_sequence(
        self, run_id: RunId, *, after_sequence: int
    ) -> None: ...

@runtime_checkable
class AgentOutputPolicy(Protocol):
    async def authorize_provider_chunk(
        self,
        spec: OutputStreamSpec,
        chunk: ModelStreamChunk,
    ) -> ModelStreamChunk | None: ...
~~~

append_event allocates the next Turn sequence, inserts one row and returns the committed row inside DatabaseConnection.transaction(cancellation_linearizable=True). commit_run_lifecycle accepts only an AgentRunController-authorized RunCommit and atomically applies its state mutation plus the matching canonical lifecycle event through shared run-store helpers; a status/event mismatch is rejected before SQL. commit_stream verifies status=open; for final_public it concatenates only persisted Provider content deltas and writes the Conversation projection in the same transaction. Publisher is only a commit notification/wake-up channel and retains the highest published sequence per Run; subscribers always read event bodies from the repository, so a notification race cannot create a second truth.

Change DomainEventProjector.project(run_id, effect) to return None and accept only DomainEffectOutput. The SQLite output repository invokes it inside the same event transaction; because it never receives or returns AgentOutputEvent, it cannot rewrite source, visibility, channel, sequence or terminal state.

- [ ] **Step 5: Run persistence tests**

Run: .venv/bin/python -m pytest backend/tests/test_sqlite_agent_output_repository.py backend/tests/test_purra_sqlite_run_repository.py -q

Expected: PASS; injected insert failure produces no published/queryable event, duplicate source keys do not advance sequence, and partial final streams never create Conversation answers.

- [ ] **Step 6: Commit the canonical persistence layer**

~~~bash
git add packages/purra/src/purra/output/ports.py packages/purra/src/purra/ports/__init__.py packages/purra/src/purra/ports/projection.py backend/database/schema.py backend/infrastructure/persistence/sqlite_agent_output_repository.py backend/infrastructure/persistence/sqlite_run_repository.py backend/tests/test_sqlite_agent_output_repository.py backend/tests/test_purra_sqlite_run_repository.py
git commit -m "feat(agent): persist canonical output journal"
~~~

### Task 3: Replace Every Provider Call with AgentModelInvocationManager

**Files:**
- Create: packages/purra/src/purra/model_invocation/__init__.py
- Create: packages/purra/src/purra/model_invocation/contracts.py
- Create: packages/purra/src/purra/model_invocation/manager.py
- Modify: packages/purra/src/purra/model_execution.py
- Modify: packages/purra/src/purra/planner.py
- Modify: packages/purra/src/purra/runtime/orchestrator.py
- Modify: packages/purra/src/purra/engine/orchestrator.py
- Create: packages/purra/tests/test_model_invocation_manager.py
- Modify: packages/purra/tests/test_model_execution.py
- Modify: backend/tests/test_purra_runtime.py

**Interfaces:**
- Consumes: ModelGateway, Task 1 intents/modes, Task 2 output repository port.
- Produces: AgentModelCall, ModelInvocationReceipt, ManagedInvocationStream, AgentModelInvocationManager.stream() and complete().

- [ ] **Step 1: Write fail-closed invocation tests**

~~~python
async def test_live_full_text_validation_is_rejected_before_gateway_call():
    call = AgentModelCall(
        request=request,
        output_intent=AgentOutputIntent.FINAL_PUBLIC,
        commit_mode=OutputCommitMode.LIVE,
        requires_full_text_validation=True,
    )
    with pytest.raises(ContractViolationError):
        await manager.stream(messages, call, context)
    assert gateway.calls == []

async def test_public_chunk_is_observed_before_runtime_consumes_it():
    managed = await manager.stream(messages, live_call, context)
    chunk = await anext(managed.chunks)
    assert output_observer.accepted == [chunk]
~~~

- [ ] **Step 2: Run manager and Runtime tests to establish red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_model_invocation_manager.py packages/purra/tests/test_model_execution.py backend/tests/test_purra_runtime.py -q

Expected: FAIL because Runtime/Planner still own Gateway access and calls have no immutable output intent.

- [ ] **Step 3: Implement the immutable model-call boundary**

~~~python
@dataclass(frozen=True, slots=True)
class AgentModelCall:
    request: ModelRequest
    output_intent: AgentOutputIntent
    commit_mode: OutputCommitMode
    requires_full_text_validation: bool = False
    reasoning_mode: ReasoningMode = ReasoningMode.DEFAULT
    output_limit: InvocationOutputLimit | None = None

@dataclass(frozen=True, slots=True)
class ModelInvocationReceipt:
    invocation_id: str
    output_stream_id: str
    model: str
    output_intent: AgentOutputIntent
    commit_mode: OutputCommitMode
    output_limit: InvocationOutputLimit
    call_parameters: tuple[Mapping[str, JsonValue], ...]
~~~

The manager resolves capability/output limit, opens the output stream, starts the model Operation, calls Gateway, forwards each raw ModelStreamChunk exactly once to the output processor observer, classifies termination/usage/evidence, then finishes or aborts the stream and Operation.

- [ ] **Step 4: Migrate model call sites with fixed semantics**

| Call site | intent | mode |
| --- | --- | --- |
| Planner / repair | structured_private | private |
| Judge / validator model call | structured_private | private |
| Context compaction | structured_private | private |
| Tool-capable runtime round | structured_private | private until a dedicated public round |
| Ordinary final answer | final_public | live |
| Validated candidate | structured_private | gated |
| Optional committed-result presentation | final_public | live |

Retain ManagedModelExecutor only as a temporary internal alias that delegates to the manager inside this task; Task 8 deletes the alias after every host call site moves.

- [ ] **Step 5: Run model/runtime tests and a Gateway usage scan**

Run: .venv/bin/python -m pytest packages/purra/tests/test_model_invocation_manager.py packages/purra/tests/test_model_execution.py backend/tests/test_purra_runtime.py backend/tests/test_purra_planner.py backend/tests/test_purra_context_enhancement.py -q

Run: rg -n 'ModelGateway|\.stream\(|\.complete\(' packages/purra/src/purra backend/application backend/domains backend/infrastructure

Expected: tests PASS; concrete Gateway use appears only in provider adapters, composition wiring and model_invocation/manager.py.

- [ ] **Step 6: Commit the single model invocation boundary**

~~~bash
git add packages/purra/src/purra/model_invocation packages/purra/src/purra/model_execution.py packages/purra/src/purra/planner.py packages/purra/src/purra/runtime/orchestrator.py packages/purra/src/purra/engine/orchestrator.py packages/purra/tests/test_model_invocation_manager.py packages/purra/tests/test_model_execution.py backend/tests/test_purra_runtime.py
git commit -m "refactor(agent): centralize provider invocations"
~~~

### Task 4: Establish AgentOutputProcessor as the Only Outward Output Ingress

**Files:**
- Create: packages/purra/src/purra/output/processor.py
- Modify: packages/purra/src/purra/output/contracts.py
- Modify: packages/purra/src/purra/model_invocation/manager.py
- Modify: packages/purra/src/purra/events.py
- Modify: packages/purra/src/purra/runtime/orchestrator.py
- Create: packages/purra/tests/test_output_processor.py
- Modify: backend/tests/test_purra_runtime.py

**Interfaces:**
- Consumes: AgentOutputRepository, AgentOutputPublisher, AgentOutputPolicy, ModelInvocationReceipt, Provider ModelStreamChunk and closed Operation/Run/Tool/Domain event contracts.
- Produces: AgentOutputProcessor.open_model_stream(), accept_provider_chunk(), accept_operation_event(), accept_run_lifecycle_event(), accept_tool_event(), accept_domain_effect_event(), finish_model_stream(), abort_model_stream().

- [ ] **Step 1: Write ownership, ordering and true-stream tests**

~~~python
async def test_only_provider_chunk_can_create_public_text():
    await processor.accept_operation_event(operation_started)
    await processor.accept_tool_event(tool_completed)
    await processor.accept_run_lifecycle_event(run_completed)
    assert [
        e for e in repository.events
        if e.visibility is OutputVisibility.PUBLIC
        and e.channel in {OutputChannel.COMMENTARY, OutputChannel.FINAL}
    ] == []

async def test_live_chunk_is_persisted_then_published_without_rechunking():
    await processor.accept_provider_chunk(stream_id, provider_chunk("甲乙"))
    assert repository.committed_text == ["甲乙"]
    assert publisher.published_text == ["甲乙"]
    assert publisher.calls[0].happened_after(repository.commit_marker)

async def test_execution_public_provider_chunk_uses_commentary_channel():
    await processor.accept_provider_chunk(
        execution_public_stream_id, provider_chunk("我会先核对当前正文")
    )
    assert publisher.published[-1].channel is OutputChannel.COMMENTARY
    assert publisher.published[-1].source is OutputSource.PROVIDER

async def test_gated_and_private_chunks_never_publish():
    await processor.accept_provider_chunk(gated_stream_id, provider_chunk("候选"))
    await processor.accept_provider_chunk(private_stream_id, provider_chunk("规划"))
    assert publisher.published == []

async def test_persistence_failure_never_creates_ghost_public_event():
    repository.fail_next_append("disk_full")
    with pytest.raises(OutputPersistenceError):
        await processor.accept_provider_chunk(stream_id, provider_chunk("不可见"))
    assert publisher.published == []
    assert recovery_signals[-1].code == "output_persistence_failed"
~~~

- [ ] **Step 2: Run processor tests and verify red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_output_processor.py backend/tests/test_purra_runtime.py -q

Expected: FAIL because Runtime still emits assistant.* events and no processor owns persistence-before-publish.

- [ ] **Step 3: Implement the closed processor API**

~~~python
class AgentOutputProcessor:
    async def open_model_stream(
        self,
        receipt: ModelInvocationReceipt,
        spec: OutputStreamSpec,
    ) -> OutputStreamSpec: ...

    async def accept_provider_chunk(
        self,
        output_stream_id: str,
        chunk: ModelStreamChunk,
    ) -> tuple[AgentOutputEvent, ...]: ...

    async def accept_operation_event(
        self,
        event: OperationStarted | OperationFinished,
    ) -> AgentOutputEvent: ...

    async def accept_run_lifecycle_event(
        self,
        commit: RunCommit,
        event: RunLifecycleOutputDraft,
    ) -> AgentOutputEvent: ...

    async def accept_tool_event(
        self,
        event: ToolOutputEvent,
    ) -> AgentOutputEvent: ...

    async def accept_domain_effect_event(
        self,
        event: DomainEffectOutput,
    ) -> AgentOutputEvent: ...
~~~

Only accept_provider_chunk maps content_delta to commentary/final public text, based on immutable stream intent. reasoning_delta is diagnostic/private. Tool call deltas and usage remain typed private/structured fields. Every method authorizes and normalizes, calls repository, then publishes the returned committed event; policy can allow, redact or reject but cannot create replacement text. accept_run_lifecycle_event uses repository.commit_run_lifecycle so Run terminal state and its canonical event commit atomically; it never appends a second terminal event.

- [ ] **Step 4: Remove public text construction from Runtime**

Delete CoreEventType.ASSISTANT_COMMENTARY_DELTA and ASSISTANT_FINAL_DELTA. Runtime emits no AgentEvent containing public text; ModelManager hands raw chunks to OutputProcessor and separately returns tool-call/reasoning/finish data needed by Runtime. Keep MODEL_CONTENT_DELTA only as a private compatibility name until all tests migrate in this task, then replace it with canonical OutputEventKind.PROVIDER_CONTENT_DELTA.

- [ ] **Step 5: Run true-stream and source-authentication tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_output_processor.py packages/purra/tests/test_model_invocation_manager.py backend/tests/test_purra_runtime.py backend/tests/test_sse_private_protocol_projection.py -q

Expected: PASS; a delayed fake Provider publishes its first public event before Provider finish, chunk content/count/order are identical, persistence failure produces a typed recoverable failure and no ghost event, and Runtime/Tool/Domain attempts to pass a public channel fail by type or contract validation.

- [ ] **Step 6: Commit the sole output ingress**

~~~bash
git add packages/purra/src/purra/output/processor.py packages/purra/src/purra/output/contracts.py packages/purra/src/purra/model_invocation/manager.py packages/purra/src/purra/events.py packages/purra/src/purra/runtime/orchestrator.py packages/purra/tests/test_output_processor.py backend/tests/test_purra_runtime.py
git commit -m "feat(agent): centralize canonical output processing"
~~~

### Task 5: Give Operation Timing to AgentOperationController

**Files:**
- Create: packages/purra/src/purra/operations/controller.py
- Modify: packages/purra/src/purra/timing.py
- Modify: packages/purra/src/purra/model_invocation/manager.py
- Modify: packages/purra/src/purra/tools/executor.py
- Modify: packages/purra/src/purra/context_orchestration/compaction.py
- Modify: packages/purra/src/purra/runtime/orchestrator.py
- Create: packages/purra/tests/test_operation_controller.py
- Modify: backend/tests/test_purra_tool_executor.py
- Modify: backend/tests/test_conversation_compaction.py

**Interfaces:**
- Consumes: Task 1 Operation contracts and Task 4 AgentOutputProcessor.accept_operation_event().
- Produces: OperationReceipt; AgentOperationController.start(), succeed(), fail(), cancel(); authoritative started_at, finished_at and duration_ms.

- [ ] **Step 1: Write monotonic-duration and unique-terminal tests**

~~~python
async def test_duration_uses_monotonic_clock_but_persists_wall_timestamps():
    receipt = await controller.start(OperationKind.TOOL, scope)
    wall_clock.jump_back(seconds=60)
    monotonic.advance(milliseconds=37)
    finished = await controller.succeed(receipt.operation_id)
    assert finished.duration_ms == 37
    assert finished.finished_at < receipt.started_at

async def test_operation_has_exactly_one_terminal_event():
    await controller.succeed(operation_id)
    with pytest.raises(ContractViolationError):
        await controller.fail(operation_id, "late_failure")

async def test_refresh_rebuilds_running_timer_from_persisted_start():
    await controller.start(OperationKind.TOOL, scope)
    restored = replay_operations(await output_repository.list_events(run_id))
    assert restored[operation_id].started_at == persisted_started_at
    assert restored[operation_id].status is OperationStatus.RUNNING
~~~

- [ ] **Step 2: Run operation and tool tests to establish red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_operation_controller.py backend/tests/test_purra_tool_executor.py backend/tests/test_conversation_compaction.py -q

Expected: FAIL because tool/model/compaction timing is trace-local and not represented as paired canonical events.

- [ ] **Step 3: Implement the lifecycle owner**

~~~python
@dataclass(frozen=True, slots=True)
class OperationReceipt:
    operation_id: str
    kind: OperationKind
    run_id: RunId
    invocation_id: str | None
    started_at: datetime

class AgentOperationController:
    async def start(
        self, kind: OperationKind, scope: OperationScope
    ) -> OperationReceipt: ...
    async def succeed(
        self, operation_id: str, display: OperationDisplay = OperationDisplay()
    ) -> OperationFinished: ...
    async def fail(
        self, operation_id: str, error_code: str
    ) -> OperationFinished: ...
    async def cancel(
        self, operation_id: str, error_code: str = "operation_canceled"
    ) -> OperationFinished: ...
~~~

start records wall clock plus an in-memory monotonic baseline. Terminal methods use monotonic elapsed duration, persist the wall-clock terminal timestamp, reject duplicate/missing starts, and pass events to OutputProcessor. Persisted display metadata is limited to stable identifiers and localized label keys; business cannot set status or duration. Browser refresh rebuilds a running timer from persisted startedAt; OutputProcessor never starts a timer and emittedAt is never used as operation time.

- [ ] **Step 4: Integrate all five operation kinds**

- ModelManager surrounds every Provider attempt with MODEL.
- CoreToolExecutor surrounds each individual ToolCall, not only the batch, with TOOL.
- Validator/Judge execution uses VALIDATION.
- ContextCompressionCoordinator uses CONTEXT_COMPACTION.
- AgentDelegationCoordinator in Task 7 uses DELEGATION.

Remove duration calculation from output projection and UI payload builders. Keep aggregate trace duration only as diagnostics derived from operation events.

- [ ] **Step 5: Run focused lifecycle tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_operation_controller.py packages/purra/tests/test_model_invocation_manager.py backend/tests/test_purra_tool_executor.py backend/tests/test_conversation_compaction.py backend/tests/test_purra_runtime.py -q

Expected: PASS; every start has one terminal, tool events contain operationId/startedAt/finishedAt/durationMs, and cancellation produces one canceled terminal.

- [ ] **Step 6: Commit authoritative operation timing**

~~~bash
git add packages/purra/src/purra/operations/controller.py packages/purra/src/purra/timing.py packages/purra/src/purra/model_invocation/manager.py packages/purra/src/purra/tools/executor.py packages/purra/src/purra/context_orchestration/compaction.py packages/purra/src/purra/runtime/orchestrator.py packages/purra/tests/test_operation_controller.py backend/tests/test_purra_tool_executor.py backend/tests/test_conversation_compaction.py
git commit -m "feat(agent): own operation lifecycle timing"
~~~

### Task 6: Decouple Run Execution from Subscription with AgentRunSupervisor

**Files:**
- Create: packages/purra/src/purra/execution/__init__.py
- Create: packages/purra/src/purra/execution/handle.py
- Create: packages/purra/src/purra/execution/supervisor.py
- Modify: packages/purra/src/purra/api/__init__.py
- Modify: packages/purra/src/purra/engine/orchestrator.py
- Modify: packages/purra/src/purra/engine/durable_execution.py
- Modify: packages/purra/src/purra/cancellation.py
- Create: packages/purra/tests/test_run_supervisor.py
- Modify: backend/tests/test_purra_engine.py
- Modify: backend/tests/test_purra_runtime_cancellation_race.py

**Interfaces:**
- Consumes: AgentOutputRepository.list_events(), RunRepository lease/cancellation operations, AgentCore internal execute coroutine.
- Produces: AgentRunHandle.run_id, subscribe(after_sequence), wait(), cancel(reason); AgentCore.submit(request, options).

- [ ] **Step 1: Write disconnect, reconnect and explicit-cancel tests**

~~~python
async def test_closing_subscription_does_not_cancel_run():
    handle = await core.submit(request, options=options)
    stream = handle.subscribe(after_sequence=0)
    await anext(stream)
    await stream.aclose()
    assert await handle.wait() == completed_result

async def test_reconnect_reads_only_events_after_cursor():
    first_page = await collect_until(handle.subscribe(0), sequence=3)
    second_page = await collect(handle.subscribe(after_sequence=3))
    assert [e.sequence for e in second_page] == [4, 5, 6]

async def test_explicit_cancel_produces_one_terminal():
    await handle.cancel("user_requested")
    result = await handle.wait()
    assert result.outcome is RuntimeOutcome.CANCELED
    assert count_terminal_events(result.run_id) == 1
~~~

- [ ] **Step 2: Run supervisor/cancellation tests to establish red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_run_supervisor.py backend/tests/test_purra_engine.py backend/tests/test_purra_runtime_cancellation_race.py -q

Expected: FAIL because AgentCore.run() owns execution through an async iterator and iterator close maps to consumer_disconnected.

- [ ] **Step 3: Implement the public handle and server-owned task**

~~~python
@runtime_checkable
class AgentRunHandle(Protocol):
    @property
    def run_id(self) -> RunId: ...
    def subscribe(
        self, after_sequence: int = 0
    ) -> AsyncIterator[AgentOutputEvent]: ...
    async def wait(self) -> AgentRunResult: ...
    async def cancel(self, reason: str) -> None: ...

class AgentCore:
    async def submit(
        self,
        request: AgentRunRequest,
        *,
        options: AgentCoreRunOptions | None = None,
    ) -> AgentRunHandle: ...
~~~

AgentRunSupervisor creates and owns the asyncio Task, acquires/renews/releases the execution lease, watches the persisted cancellation command and completes a shared result future. subscribe first replays repository events after cursor and then listens for newly committed sequence values. Closing a subscription only unregisters that subscriber.

- [ ] **Step 4: Delete transport-owned cancellation semantics**

Remove CoreCommandType.CLIENT_DISCONNECTED, the consumer_disconnected cancellation path, iterator-finally cancellation and application track_background_run ownership. Keep AgentCore.run() only as a test migration wrapper implemented as submit + subscribe + wait within this task; Task 9 deletes it after routes move.

- [ ] **Step 5: Run lifecycle and restart/reconnect tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_run_supervisor.py backend/tests/test_purra_engine.py backend/tests/test_purra_runtime_cancellation_race.py backend/tests/test_purra_reserved_lifecycle.py backend/tests/test_screenplay_agent_durable_service.py -q

Expected: PASS; disconnect leaves lease and task active, explicit cancel is linearizable, and replay returns committed events without duplicates.

- [ ] **Step 6: Commit the execution handle**

~~~bash
git add packages/purra/src/purra/execution packages/purra/src/purra/api/__init__.py packages/purra/src/purra/engine/orchestrator.py packages/purra/src/purra/engine/durable_execution.py packages/purra/src/purra/cancellation.py packages/purra/tests/test_run_supervisor.py backend/tests/test_purra_engine.py backend/tests/test_purra_runtime_cancellation_race.py
git commit -m "refactor(agent): separate run execution from subscriptions"
~~~

### Task 7: Move Parent/Child Run Semantics into AgentDelegationCoordinator

**Files:**
- Create: packages/purra/src/purra/delegation/__init__.py
- Create: packages/purra/src/purra/delegation/coordinator.py
- Modify: packages/purra/src/purra/ports/persistence.py
- Modify: packages/purra/src/purra/engine/orchestrator.py
- Modify: backend/application/agent_delegation_service.py
- Modify: backend/application/agent_delegation_tool.py
- Modify: backend/application/agent_run_service.py
- Create: packages/purra/tests/test_delegation_coordinator.py
- Modify: backend/tests/test_agent_delegation_service.py

**Interfaces:**
- Consumes: DelegationRepository, AgentCore.submit(), AgentOperationController, AgentOutputProcessor, role-specific child request factory registered by the product.
- Produces: AgentDelegationCoordinator.create(), claim_and_submit(), record_result(), cancel_children(), aggregate(); canonical sourceRunId/parentRunId/invocationId federation.

- [ ] **Step 1: Write parent/child federation tests**

~~~python
async def test_child_events_receive_parent_turn_sequence_and_keep_source_ids():
    child_handle = await coordinator.claim_and_submit(delegation_id)
    await child_handle.wait()
    parent_events = await output_repository.list_events(parent_run_id, after_sequence=0)
    assert [e.sequence for e in parent_events] == sorted(e.sequence for e in parent_events)
    assert any(e.payload["sourceRunId"] == child_handle.run_id for e in parent_events)

async def test_cancel_parent_cancels_children_once():
    await coordinator.cancel_children(parent_run_id)
    await coordinator.cancel_children(parent_run_id)
    assert child_handle.cancel_calls == 1
~~~

- [ ] **Step 2: Run delegation tests to establish red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_delegation_coordinator.py backend/tests/test_agent_delegation_service.py -q

Expected: FAIL because AgentRunService manually constructs delegation.event, persists selected child events and closes child streams.

- [ ] **Step 3: Implement generic delegation coordination**

~~~python
@runtime_checkable
class ChildRunRequestFactory(Protocol):
    async def build(
        self, claim: DelegationClaim
    ) -> tuple[AgentRunRequest, AgentCoreRunOptions]: ...

class AgentDelegationCoordinator:
    async def claim_and_submit(
        self, delegation_id: str
    ) -> AgentRunHandle: ...
    async def record_result(
        self, delegation_id: str, result: AgentRunResult
    ) -> DelegationAggregation: ...
    async def cancel_children(self, parent_run_id: RunId) -> int: ...
~~~

The coordinator starts a DELEGATION Operation, attaches the child Run, subscribes to the child's canonical journal, appends typed federated events through OutputProcessor, records the result exactly once and finishes the Operation. Role catalogs and prompt construction remain product registrations behind ChildRunRequestFactory.

- [ ] **Step 4: Delete application-layer multiplexing**

Remove the queue/publish/run_child/delegation.event persistence block from AgentRunService and remove AgentComposition.append_run_event(). AgentDelegationService becomes a thin product view/registration adapter; it cannot assign canonical sequence or write events.

- [ ] **Step 5: Run delegation, composition and replay tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_delegation_coordinator.py backend/tests/test_agent_delegation_service.py backend/tests/test_agent_composition.py backend/tests/test_agent_run_queries.py -q

Expected: PASS; parallel child events have stable parent Turn sequence, retain source IDs, reconnect matches live state, and no application method can append an arbitrary Run event.

- [ ] **Step 6: Commit delegation ownership**

~~~bash
git add packages/purra/src/purra/delegation packages/purra/src/purra/ports/persistence.py packages/purra/src/purra/engine/orchestrator.py backend/application/agent_delegation_service.py backend/application/agent_delegation_tool.py backend/application/agent_run_service.py packages/purra/tests/test_delegation_coordinator.py backend/tests/test_agent_delegation_service.py
git commit -m "refactor(agent): centralize delegation lifecycle"
~~~

### Task 8: Implement the Two Generic Response Transactions

**Files:**
- Modify: packages/purra/src/purra/output/contracts.py
- Modify: packages/purra/src/purra/output/ports.py
- Create: packages/purra/src/purra/output/response_transaction.py
- Modify: packages/purra/src/purra/engine/options.py
- Modify: packages/purra/src/purra/engine/orchestrator.py
- Modify: packages/purra/src/purra/runtime/orchestrator.py
- Create: packages/purra/tests/test_response_transaction.py
- Modify: backend/tests/test_purra_response_validation.py
- Modify: backend/tests/test_purra_engine.py

**Interfaces:**
- Consumes: validated AgentRunResult/Artifact references, AgentModelInvocationManager, AgentOutputProcessor and product-registered public fact provider.
- Produces: ResponseTransactionMode, PublicPresentationMode, ResponseTransactionPolicy, PublicFact, PublicFactBundle, CommittedResultFactsProvider and AgentResponseTransaction.

- [ ] **Step 1: Write direct-live and validated-result tests**

~~~python
async def test_direct_answer_streams_before_provider_finish():
    result_task = asyncio.create_task(transaction.execute_direct(request))
    first = await published_events.get()
    assert first.payload["delta"] == "第一块"
    assert not provider.finished.is_set()
    await result_task

async def test_validated_candidate_is_private_until_commit():
    committed = await transaction.execute_validated(request, candidate_port)
    assert published_before_validation == []
    assert committed.artifact_id == "artifact-1"

async def test_public_presentation_uses_only_committed_facts_and_no_tools():
    await transaction.present(committed)
    assert gateway.last_messages == facts.as_messages()
    assert gateway.last_invocation.tools == ()

async def test_missing_public_presentation_does_not_inject_template_text():
    await transaction.execute_validated(no_presentation_policy, candidate_port)
    assert public_text_events == []

def test_public_fact_bundle_rejects_internal_ids_and_unbounded_body():
    with pytest.raises(ContractViolationError):
        PublicFactBundle(facts=(
            PublicFact("reviewId", "review_internal_123"),
            PublicFact("contentText", "x" * 20_000),
        ))
~~~

- [ ] **Step 2: Run transaction/validation tests to establish red**

Run: .venv/bin/python -m pytest packages/purra/tests/test_response_transaction.py backend/tests/test_purra_response_validation.py backend/tests/test_purra_engine.py -q

Expected: FAIL because Runtime currently buffers/repairs one candidate and then directly emits or returns it as the final Assistant answer.

- [ ] **Step 3: Define the generic transaction policy**

~~~python
class ResponseTransactionMode(StrEnum):
    DIRECT_LIVE = "direct_live"
    VALIDATED_RESULT = "validated_result"

class PublicPresentationMode(StrEnum):
    NONE = "none"
    MODEL_LIVE = "model_live"

@dataclass(frozen=True, slots=True)
class ResponseTransactionPolicy:
    mode: ResponseTransactionMode
    public_presentation: PublicPresentationMode = PublicPresentationMode.NONE

@dataclass(frozen=True, slots=True)
class PublicFact:
    key: str
    value: JsonValue

@dataclass(frozen=True, slots=True)
class PublicFactBundle:
    facts: tuple[PublicFact, ...]
    resource_refs: tuple[str, ...] = ()

@runtime_checkable
class CommittedResultFactsProvider(Protocol):
    async def facts_for(
        self, run_id: RunId, result: AgentRunResult
    ) -> PublicFactBundle: ...
~~~

AgentCoreRunOptions receives response_transaction_policy and committed_result_facts_provider. Validation rejects MODEL_LIVE without a facts provider and DIRECT_LIVE when response validators/judges require complete text. PublicFactBundle enforces a small total serialized size, a closed resource-reference shape and forbidden internal fields such as database IDs, candidate/report bodies, traces and private status.

- [ ] **Step 4: Implement the generic ordering**

DIRECT_LIVE creates one final_public/live invocation and commits it after Provider finish.

VALIDATED_RESULT creates a structured_private/gated invocation, collects it privately, runs every validator/judge as VALIDATION Operations, commits the typed result/Artifact through registered ports, obtains bounded PublicFactBundle, then either stops with structured state or creates a separate final_public/live no-tool invocation. If that final invocation fails, retry only that invocation/stream; do not rerun candidate generation, tools, validation, Artifact finalize or Revision publication.

- [ ] **Step 5: Delete same-stream full-text-public repair behavior**

Remove Runtime branches that publish buffered validated text, emit deferred process announcements, or replace a public response after validation. Keep private repair retries bounded inside the candidate transaction. After the first public live delta, only stream abort/new-stream retry is legal.

- [ ] **Step 6: Run transaction, artifact and recovery tests**

Run: .venv/bin/python -m pytest packages/purra/tests/test_response_transaction.py backend/tests/test_purra_response_validation.py backend/tests/test_purra_engine.py backend/tests/test_purra_artifact_continuity.py backend/tests/test_purra_recovery.py -q

Expected: PASS; validated candidates have zero public deltas before commit, a final presentation failure does not increment tool/Artifact/Revision side-effect counters, and no host-authored fallback answer appears.

- [ ] **Step 7: Commit generic response transactions**

~~~bash
git add packages/purra/src/purra/output/contracts.py packages/purra/src/purra/output/ports.py packages/purra/src/purra/output/response_transaction.py packages/purra/src/purra/engine/options.py packages/purra/src/purra/engine/orchestrator.py packages/purra/src/purra/runtime/orchestrator.py packages/purra/tests/test_response_transaction.py backend/tests/test_purra_response_validation.py backend/tests/test_purra_engine.py
git commit -m "feat(agent): add validated response transactions"
~~~

### Task 9: Migrate Application, Writing and Screenplay to the Stable PurrA Entry

**Files:**
- Modify: backend/application/agent_composition.py
- Modify: backend/application/composition_factory.py
- Modify: backend/application/agent_run_service.py
- Modify: backend/application/request_mapping.py
- Modify: backend/application/run_execution_control.py
- Modify: backend/application/sse_mapping.py
- Modify: backend/application/response_judging.py
- Modify: backend/application/memory_reranking.py
- Modify: backend/infrastructure/models/model_conversation_summarizer.py
- Modify: backend/application/screenplay_agent_planner.py
- Modify: backend/application/screenplay_agent_service.py
- Modify: backend/application/screenplay_agent_task_executor.py
- Modify: backend/application/screenplay_structured_call.py
- Modify: backend/application/screenplay_tool_calling.py
- Modify: backend/application/screenplay_agent_stream.py
- Delete: backend/application/screenplay_progress_stream.py
- Create: backend/domains/screenplay_agent/public_facts.py
- Create: backend/domains/writing/public_facts.py
- Modify: backend/infrastructure/persistence/run_conversation_store.py
- Modify: backend/routers/ai.py
- Modify: backend/routers/screenplay_conversations.py
- Modify: packages/purra/src/purra/model_execution.py
- Modify: packages/purra/src/purra/api/__init__.py
- Modify: backend/tests/test_ai_composed_sse_wire_contract.py
- Modify: backend/tests/test_agent_refactor_boundaries.py
- Modify: backend/tests/test_purra_boundaries.py
- Modify: backend/tests/test_screenplay_agent_rewrite.py
- Modify: backend/tests/test_screenplay_agent_routes.py
- Modify: backend/tests/test_writing_domain_boundaries.py

**Interfaces:**
- Consumes: AgentCore.submit(), AgentRunHandle, AgentOutputEvent and response transaction policy/facts provider.
- Produces: one canonical application adapter for AI and Screenplay routes; Writing/Screenplay profile registrations without runtime ownership.

- [ ] **Step 1: Write adapter conformance tests before migration**

~~~python
def test_screenplay_and_writing_use_the_same_public_core_api():
    assert production_purra_imports("backend/application") <= {
        "purra.api", "purra.contracts", "purra.output", "purra.ports"
    }

def test_product_ast_has_no_internal_runtime_dependencies():
    imports = parse_production_imports()
    assert not imports.intersection({
        "purra.runtime",
        "purra.run_controller",
        "purra.model_invocation.manager",
    })

async def test_sse_disconnect_leaves_submitted_run_active(client):
    response = await client.stream("POST", "/api/ai/stream", json=body)
    await response.aclose()
    assert await run_repository.status(run_id) == "running"

async def test_screenplay_review_has_no_host_or_projected_public_copy():
    chunks = await run_screenplay_review()
    assert not any("processSummary" in event.public_text for event in chunks)
    assert not any(event.source != "provider" and event.public_text for event in chunks)

async def test_admission_receipt_and_localized_error_are_structured_only():
    events = await run_admitted_or_failed_task()
    assert all(
        event.channel not in {"commentary", "final"}
        for event in events
        if event.source != "provider"
    )
~~~

- [ ] **Step 2: Run application/route tests to establish red**

Run: .venv/bin/python -m pytest backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_boundaries.py backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_routes.py backend/tests/test_writing_domain_boundaries.py -q

Expected: FAIL on direct Runtime/ManagedModelExecutor use, Screenplay chunk projection, route-owned iterator lifetime and host-written terminal conversation text.

- [ ] **Step 3: Rewire composition and routes**

AgentComposition and composition_factory supply only infrastructure ports—ModelGateway, output repository/publisher/policy, run/lease/delegation repositories, clocks and product registrations—to AgentCore. AgentCore constructs and owns ModelManager, OutputProcessor, OperationController, Supervisor and DelegationCoordinator; product composition cannot call them independently. AgentRunService calls submit, route SSE calls handle.subscribe(cursor), non-streaming callers call handle.wait, and route cleanup closes only the subscription. run_execution_control loses background-task ownership and becomes an explicit cancel/control-plane adapter. sse_mapping serializes AgentOutputEvent fields without choosing delta/commentary/error semantics.

- [ ] **Step 4: Map Writing and Screenplay through generic response policies**

- Ordinary answer request maps to DIRECT_LIVE.
- Writing continuity/count/summary-length validation maps to VALIDATED_RESULT plus backend/domains/writing/public_facts.py implementing CommittedResultFactsProvider.
- Screenplay candidate/review validation maps to VALIDATED_RESULT; backend/domains/screenplay_agent/public_facts.py exposes only committed status, user-facing version label and resource reference, never Revision ID, report body or candidate payload.
- A product that does not require a chat conclusion selects PublicPresentationMode.NONE and receives no replacement sentence.

Delete direct model invocation from response_judging.py, memory_reranking.py, model_conversation_summarizer.py and every screenplay application module; register their private operations with ModelManager through composition ports.

- [ ] **Step 5: Remove legacy text output and aliases**

Delete JsonStringFieldProjector, visible_execution_progress, visible_stream_chunks, host_progress, progress_items, processSummary/executionSummary public projection, ScreenplayAgentChunkProjector final delta generation, host-written failed/canceled/blocked Conversation responses, TaskAdmissionDecision.message/LongTaskDispatchReceipt.message/error-localization promotion to Assistant text, ManagedModelExecutor alias and AgentCore.run() migration wrapper. Keep admission, long-task and localized error data only as structured lifecycle/error display metadata. Remove assistant_content writes unless they project a committed final_public stream.

- [ ] **Step 6: Run application, Writing and Screenplay acceptance tests**

Run: .venv/bin/python -m pytest backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_boundaries.py backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_routes.py backend/tests/test_writing_domain_boundaries.py backend/tests/test_agent_composition.py backend/tests/test_conversations_routes.py -q

Expected: PASS; both domains enter AgentCore.submit(), no business module owns Gateway/Runtime/RunController, and public text source is always provider.

- [ ] **Step 7: Run architecture scans**

Run: rg -n 'visible_stream_chunks|JsonStringFieldProjector|host_progress|progress_items|processSummary|executionSummary|append_run_event|AgentCore\.run|ManagedModelExecutor|ASSISTANT_(COMMENTARY|FINAL)_DELTA' packages/purra/src/purra backend/application backend/domains backend/infrastructure

Expected: no production matches. Test fixtures may mention forbidden names only inside negative boundary assertions.

- [ ] **Step 8: Commit application and domain migration**

~~~bash
git add packages/purra/src/purra/model_execution.py packages/purra/src/purra/api/__init__.py backend/application/agent_composition.py backend/application/composition_factory.py backend/application/agent_run_service.py backend/application/request_mapping.py backend/application/run_execution_control.py backend/application/sse_mapping.py backend/application/response_judging.py backend/application/memory_reranking.py backend/application/screenplay_agent_planner.py backend/application/screenplay_agent_service.py backend/application/screenplay_agent_task_executor.py backend/application/screenplay_structured_call.py backend/application/screenplay_tool_calling.py backend/application/screenplay_agent_stream.py backend/application/screenplay_progress_stream.py backend/domains/screenplay_agent/public_facts.py backend/domains/writing/public_facts.py backend/infrastructure/models/model_conversation_summarizer.py backend/infrastructure/persistence/run_conversation_store.py backend/routers/ai.py backend/routers/screenplay_conversations.py backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_boundaries.py backend/tests/test_ai_composed_sse_wire_contract.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_agent_routes.py backend/tests/test_writing_domain_boundaries.py
git commit -m "refactor(agent): migrate products to canonical core output"
~~~

### Task 10: Replace Frontend Chunk Guessing with One Canonical Reducer

**Files:**
- Create: src/agent-runtime/canonicalOutput.ts
- Create: src/agent-runtime/canonicalOutput.test.ts
- Modify: src/agent-runtime/chunkReplay.test.cjs
- Modify: src/Workspace/AiPanel/hooks/chat.types.ts
- Modify: src/Workspace/AiPanel/hooks/chunkHandlers/agentRun.ts
- Modify: src/Workspace/AiPanel/hooks/chunkHandlers/streaming.ts
- Modify: src/Workspace/AiPanel/hooks/chunkHandlers/terminal.ts
- Modify: src/Workspace/AiPanel/hooks/chunkHandlers/toolProgress.ts
- Modify: src/Workspace/AiPanel/hooks/chunkHandlers/toolStart.ts
- Modify: src/Workspace/AiPanel/hooks/modelRuntime.test.cjs
- Modify: src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx
- Modify: src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts
- Modify: src/Workspace/AiPanel/components/WorkLog/grouping.ts
- Modify: src/Workspace/AiPanel/components/WorkLog/index.tsx
- Modify: src/Workspace/AiPanel/components/ToolCallStatus/presentation.ts
- Modify: src/Workspace/AiPanel/components/ToolCallStatus/index.tsx
- Modify: src/Workspace/AiPanel/components/SubAgentStatusList/presentation.ts
- Modify: src/Workspace/AiPanel/components/SubAgentStatusList/index.tsx
- Modify: src/Workspace/AiPanel/index.tsx
- Modify: src/ScreenplayAgentPage/index.tsx

**Interfaces:**
- Consumes: wire AgentOutputEvent with sequence/source/kind/channel/visibility/payload/occurredAt/emittedAt.
- Produces: reduceCanonicalOutput(state, event), replayCanonicalOutput(events), lastSequence cursor and operation/message views.

- [ ] **Step 1: Write real-time/replay equivalence and UI behavior tests**

~~~typescript
test("real-time reduction equals zero-based replay", () => {
  const live = events.reduce(reduceCanonicalOutput, initialCanonicalState());
  const replayed = replayCanonicalOutput(events);
  assert.deepEqual(replayed, live);
});

test("public text uses provider deltas only", () => {
  const state = replayCanonicalOutput([
    operationEvent,
    toolEvent,
    providerFinalDelta("完成"),
  ]);
  assert.equal(state.finalText, "完成");
});

test("terminal operation freezes backend duration", () => {
  const view = operationView(startedAt, finishedAt, 48);
  assert.equal(view.durationMs, 48);
});
~~~

- [ ] **Step 2: Run frontend tests to establish red**

Run: node --experimental-strip-types --test src/agent-runtime/canonicalOutput.test.ts src/agent-runtime/chunkReplay.test.cjs src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/Workspace/AiPanel/components/ToolCallStatus/presentation.test.ts src/Workspace/AiPanel/components/SubAgentStatusList/presentation.test.ts

Expected: FAIL because real-time and persisted paths consume different chunk shapes and operation timing can fall back to local arrival time.

- [ ] **Step 3: Implement the single sequence reducer**

~~~typescript
export type CanonicalOutputEvent = {
  eventId: string;
  outputStreamId: string | null;
  runId: string;
  turnId: string | null;
  invocationId: string | null;
  sequence: number;
  source: "provider" | "runtime" | "tool" | "domain";
  kind: string;
  channel: "commentary" | "final" | "operation" | "lifecycle" | "error" | "diagnostic";
  visibility: "public" | "private" | "diagnostic";
  payload: Record<string, unknown>;
  occurredAt: string;
  emittedAt: string;
};

export function reduceCanonicalOutput(
  state: CanonicalOutputState,
  event: CanonicalOutputEvent,
): CanonicalOutputState;
~~~

The reducer ignores sequence values already applied, rejects gaps for live delivery so the client can replay from lastSequence, appends public text only for source=provider and visibility=public, and indexes Operations by operationId. Both SSE and history loaders call this same function.

- [ ] **Step 4: Render confirmed product behavior**

- Sequential running operations show current step index, not completed count.
- Parallel runs show completed count.
- Consecutive operations render one “执行了 N 个步骤” disclosure; its first expansion contains concrete operations directly, without a nested “已操作” disclosure.
- Running duration uses now - persisted startedAt; terminal duration uses persisted durationMs.
- Execution disclosure remains available while the final answer streams; the progress capsule disappears only after Run terminal and final_public commit/abort.
- If no Provider public text exists, the panel shows structured state only.

- [ ] **Step 5: Delete legacy handler semantics and run frontend gates**

Remove direct handling of delta/commentaryDelta/agentRun* compatibility fields, local timestamp inference and source-based fallback concatenation.

Run: npm run typecheck

Run: npm run test:unit

Expected: both PASS; the focused equivalence test proves refresh/replay state is byte-for-byte equal to live reduction.

- [ ] **Step 6: Commit canonical frontend consumption**

~~~bash
git add src/agent-runtime/canonicalOutput.ts src/agent-runtime/canonicalOutput.test.ts src/agent-runtime/chunkReplay.test.cjs src/Workspace/AiPanel/hooks/chat.types.ts src/Workspace/AiPanel/hooks/chunkHandlers/agentRun.ts src/Workspace/AiPanel/hooks/chunkHandlers/streaming.ts src/Workspace/AiPanel/hooks/chunkHandlers/terminal.ts src/Workspace/AiPanel/hooks/chunkHandlers/toolProgress.ts src/Workspace/AiPanel/hooks/chunkHandlers/toolStart.ts src/Workspace/AiPanel/hooks/modelRuntime.test.cjs src/Workspace/AiPanel/components/ChatMessageList/AssistantMessageBody.tsx src/Workspace/AiPanel/components/ChatMessageList/assistantTimeline.ts src/Workspace/AiPanel/components/WorkLog/grouping.ts src/Workspace/AiPanel/components/WorkLog/index.tsx src/Workspace/AiPanel/components/ToolCallStatus/presentation.ts src/Workspace/AiPanel/components/ToolCallStatus/index.tsx src/Workspace/AiPanel/components/SubAgentStatusList/presentation.ts src/Workspace/AiPanel/components/SubAgentStatusList/index.tsx src/Workspace/AiPanel/index.tsx src/ScreenplayAgentPage/index.tsx
git commit -m "refactor(ui): consume canonical agent output events"
~~~

### Task 11: Provide Auditable Legacy Runtime Cleanup and Retire Old Stores

**Files:**
- Create: backend/database/crud/screenplay_agent_runtime_cleanup.py
- Create: backend/tests/test_screenplay_agent_runtime_cleanup.py
- Modify: backend/database/screenplay_agent_schema.py
- Modify: backend/application/screenplay_agent_stream.py
- Modify: backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py
- Modify: backend/tests/test_screenplay_v2_persistence_boundaries.py

**Interfaces:**
- Consumes: project_id/session/turn/task/run relationships and explicit user confirmation at execution time.
- Produces: ScreenplayRuntimeCleanupPlan with per-table primary keys/counts; build_cleanup_plan(); apply_cleanup(plan_digest).

- [ ] **Step 1: Write exact-scope, preservation and rollback tests**

~~~python
async def test_dry_run_resolves_ids_by_foreign_keys_not_text_matching():
    plan = await build_cleanup_plan(db, project_id)
    assert plan.table_counts["screenplay_agent_chunks"] == 4
    assert unrelated_run_id not in plan.run_ids

async def test_cleanup_preserves_documents_artifacts_and_review_decisions():
    plan = await build_cleanup_plan(db, project_id)
    await apply_cleanup(db, plan.digest)
    assert await count("screenplay_revisions") == before_revisions
    assert await count("ai_agent_artifacts") == before_artifacts
    assert await count("screenplay_review_decisions") == before_decisions

async def test_any_delete_failure_rolls_back_every_table():
    with pytest.raises(InjectedFailure):
        await apply_cleanup(db, plan.digest, fail_after_table=2)
    assert await snapshot_runtime_rows() == before
~~~

- [ ] **Step 2: Run cleanup tests to establish red**

Run: .venv/bin/python -m pytest backend/tests/test_screenplay_agent_runtime_cleanup.py backend/tests/test_screenplay_v2_persistence_boundaries.py -q

Expected: FAIL because no relationship-resolved dry-run/plan digest exists and the old chunk/event tables remain production sources.

- [ ] **Step 3: Implement dry-run and digest-locked apply**

ScreenplayRuntimeCleanupPlan contains exact IDs for screenplay turns/operations/chunks/events, their projected ai_conversations rows, and directly bound generic runs/todos/delegations/long tasks/work items/approvals/output diagnostics. It explicitly reports protected table counts for projects, revisions, revision parts, source refs, deliverables, project heads, working copies, artifacts, artifact claims and review decisions. apply_cleanup recomputes the plan, compares its SHA-256 digest, then deletes only enumerated primary keys in one cancellation-linearizable transaction.

- [ ] **Step 4: Produce the real database dry-run report without deleting**

Run the repository's read-only cleanup CLI/function against the configured Electron userData database and save docs/superpowers/evidence/2026-08-11-screenplay-agent-runtime-cleanup.json. The report must contain project/session/turn/task/run IDs, per-table delete counts and protected counts; it must not contain screenplay text, prompts, API keys or Artifact bodies.

Expected: report generated, zero DELETE statements executed.

- [ ] **Step 5: Stop for explicit user approval before destructive apply**

Present the dry-run counts and plan digest. Do not call apply_cleanup and do not drop legacy tables until the user explicitly approves this exact report.

- [ ] **Step 6: After approval, apply cleanup and retire tables**

Apply the approved digest once, verify protected counts unchanged, remove production reads/writes for screenplay_agent_chunks and redundant screenplay_agent_events, then update schema initialization to drop those retired tables only after successful migration. Delete ScreenplayAgentChunkStore/Projector and any protocol_version compatibility logic.

- [ ] **Step 7: Run cleanup and persistence boundary tests**

Run: .venv/bin/python -m pytest backend/tests/test_screenplay_agent_runtime_cleanup.py backend/tests/test_screenplay_v2_persistence_boundaries.py backend/tests/test_screenplay_agent_rewrite.py -q

Expected: PASS; old output stores have no production references, protected domain data is unchanged and a second cleanup apply is a no-op.

- [ ] **Step 8: Commit cleanup tooling and approved retirement**

~~~bash
git add backend/database/crud/screenplay_agent_runtime_cleanup.py backend/tests/test_screenplay_agent_runtime_cleanup.py backend/database/screenplay_agent_schema.py backend/application/screenplay_agent_stream.py backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py backend/tests/test_screenplay_v2_persistence_boundaries.py docs/superpowers/evidence/2026-08-11-screenplay-agent-runtime-cleanup.json
git commit -m "refactor(screenplay): retire legacy agent output stores"
~~~

### Task 12: Run Release Gates and Prove Real Provider Timing

**Files:**
- Modify: backend/tests/test_screenplay_multi_model_e2e.py
- Modify: backend/tests/test_agent_refactor_boundaries.py
- Modify: backend/tests/test_purra_incident_replay.py
- Create: docs/superpowers/evidence/2026-08-11-agent-output-real-provider-e2e.md

**Interfaces:**
- Consumes: complete canonical architecture from Tasks 1-11.
- Produces: automated gate evidence plus one real Provider timestamp trace proving pre-finish visibility and no fake stream.

- [ ] **Step 1: Add release-level assertions**

~~~python
assert provider_first_delta_at < provider_finish_at
assert persisted_first_delta_at <= frontend_first_visible_at < provider_finish_at
assert all(event.source == "provider" for event in public_text_events)
assert provider_chunk_texts == canonical_public_delta_texts
assert revision_published_at <= final_answer_first_delta_at
assert tool_finished_at - tool_started_at == tool_duration_ms
~~~

The E2E also disconnects and reconnects SSE mid-run, compares final live state with zero-based replay, and verifies that a failed final presentation does not duplicate tool or Revision side effects.

- [ ] **Step 2: Run focused Python architecture and incident gates**

Run: .venv/bin/python -m pytest backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_boundaries.py backend/tests/test_purra_incident_replay.py packages/purra/tests -q

Expected: PASS with no forbidden imports, public constructors, fake-stream helpers or event source violations.

- [ ] **Step 3: Run the full refactor gate**

Run: npm run check:agent-refactor

Expected: PASS for boundary, model-contract, Screenplay acceptance, typecheck, frontend unit and full backend suites.

- [ ] **Step 4: Run a real Provider E2E**

Run: npm run test:screenplay-real-e2e

Expected: at least one configured Provider test PASS and the evidence file records Provider first delta, first canonical persistence, first frontend-visible delta, Provider finish, tool start/finish, Revision publish, final-answer first delta and final stream commit.

If the test is skipped for missing credentials, write RELEASE BLOCKER in the evidence file and do not claim the refactor is publishable.

- [ ] **Step 5: Check repository diff and production symbol absence**

Run: git diff --check

Run: rg -n 'visible_stream_chunks|JsonStringFieldProjector|append_run_event|consumer_disconnected|screenplay_agent_chunks|ASSISTANT_(COMMENTARY|FINAL)_DELTA|ManagedModelExecutor|AgentCore\.run' packages/purra/src/purra backend/application backend/domains backend/infrastructure src

Expected: git diff check PASS and no production matches. Schema migration evidence may mention retired table names only in cleanup code/tests.

- [ ] **Step 6: Commit release evidence**

~~~bash
git add backend/tests/test_screenplay_multi_model_e2e.py backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_incident_replay.py docs/superpowers/evidence/2026-08-11-agent-output-real-provider-e2e.md
git commit -m "test(agent): gate canonical real-time output"
~~~

---

## Execution Checkpoints

1. After Task 4: public text ownership and true Provider chunk streaming are independently proven.
2. After Task 7: all five generic owners exist and application code no longer owns their state machines.
3. After Task 9: Writing and Screenplay both run on AgentCore.submit() with no old production output path.
4. After Task 10: live/replay UI equivalence and confirmed operation-panel behavior are proven.
5. Task 11 pauses at the real-data dry-run until the user approves the exact destructive scope.
6. Task 12 may declare implementation complete only when npm run check:agent-refactor passes; publishable additionally requires a non-skipped real Provider E2E.
