# PurrA 模型无关长任务执行与恢复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 PurrA 以模型能力档案、稳定语义 Part、持久化 Artifact 检查点和唯一 Operation 终态完成长任务；彻底删除隐藏输出预算、`length` 原样重试、部分 Review 成功、重复最终输出和不可靠取消路径。

**Architecture:** Model Profile 只声明客观能力，PurrA Core 只消费不可变能力快照和通用 Task Requirements。Product Application 把业务任务编译成稳定 Manifest，LongTask 逐 Part 执行并只保存 Artifact 引用。Screenplay Operation 是剧本任务唯一业务权威；所有必需 Part 完成后，在一个事务内创建 Candidate Revision、写入唯一 finalization receipt 与 Assistant 最终回答。取消先持久化命令，再在事务外传播到 worker、LongTask 和 Run。

**Tech Stack:** Python 3.12、FastAPI、PurrA dataclass/Protocol contracts、SQLite、pytest、React 19、TypeScript、Node test runner。

## Global Constraints

- 新旧生产路径不得并行保留；每个迁移任务完成时删除被取代入口和测试。
- `packages/purra` 不得出现 Provider/模型名或 `screenplay`、`episode` 等业务判断。
- 用户选择的 reasoning mode 在 Planner、JSON repair、Part Run 与恢复中保持不变；不做静默 fallback。
- `length` 不得以相同 messages、Part、模型、reasoning mode 和输出上限重试。
- 所有内容型中间结果以 Artifact/Revision 为权威；LongTask、Operation、Turn 和 SSE 只保存引用、摘要、receipt 与诊断。
- 任一必需 Part 未完成时，不创建 Candidate Revision、不写 Assistant final、不展示产物面板。
- 每个任务严格按“先写失败测试 → 运行确认失败 → 最小实现 → 运行确认通过 → 提交”执行。
- 每个任务提交前运行 `git diff --check`；不得用 `--no-verify`。
- 每次提交前先运行 `git status --short`，只 `git add --` 当前 Task 的 Files 清单；禁止 `git add -A`，避免带入用户的无关改动。

---

### Task 1: 固化事故行为与架构禁区

**Files:**

- Modify: `backend/tests/fixtures/purra_incidents/2026-08-10-failure-sequences.json`
- Modify: `backend/tests/test_purra_incident_replay.py`
- Modify: `backend/tests/test_agent_refactor_boundaries.py`
- Modify: `backend/tests/test_purra_boundaries.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_agent_routes.py`

**Interfaces:**

```python
class IncidentFixtureV2(TypedDict):
    id: str
    errorCode: str
    termination: str
    requestFingerprint: str
    repeatedRequestFingerprints: list[str]
    completedPartRefs: list[str]
    expectedDisposition: str
    expectedOperationStatus: str
    reviewIssueCount: int
    finalizationCount: int
```

- [ ] 把 fixture 升级为 `schemaVersion: 2`，为截断事故记录脱敏后的请求 fingerprint；断言 `length` 之后不存在相同 fingerprint。
- [ ] 新增回放用例：分集系统失败不生成 Review Issue、不增加 verdict 计数、不产生 finalization；部分 Part 保持原引用。
- [ ] 新增边界扫描：`packages/purra/src/purra` 禁止 `deepseek|zai|kimi|mimo|screenplay|episode`；禁止生产代码引用旧任务预算符号和截断重试指导语。
- [ ] 新增取消路由失败测试：相同 `Idempotency-Key` 必须返回同一 receipt；当前路由丢弃 key，因此此时测试应失败。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_purra_incident_replay.py backend/tests/test_agent_refactor_boundaries.py backend/tests/test_purra_boundaries.py backend/tests/test_screenplay_agent_routes.py -q`
- [ ] 确认新断言因现有原样重试、Review 污染、取消 receipt 缺失而失败，而不是 fixture/导入错误。
- [ ] Commit: `git commit -m "test(agent): freeze long-task failure invariants"`

### Task 2: 建立版本化模型能力快照与 Task Requirements

**Files:**

- Modify: `packages/purra/src/purra/model_protocol/capabilities.py`
- Create: `packages/purra/src/purra/model_protocol/requirements.py`
- Modify: `packages/purra/src/purra/model_protocol/__init__.py`
- Modify: `packages/purra/src/purra/contracts/__init__.py`
- Modify: `backend/database/schema.py`
- Modify: `backend/infrastructure/models/profiles/base.py`
- Modify: `backend/infrastructure/models/profiles/registry.py`
- Modify: `backend/infrastructure/models/profiles/deepseek_v4.py`
- Modify: `backend/infrastructure/models/profiles/glm5_2.py`
- Modify: `backend/infrastructure/models/profiles/kimi_k2_6.py`
- Modify: `backend/infrastructure/models/profiles/kimi_k3.py`
- Modify: `backend/infrastructure/models/profiles/mimo_v2_5_pro.py`
- Modify: `backend/infrastructure/models/profiles/minimax_m3.py`
- Modify: `backend/application/model_runtime.py`
- Modify: `backend/application/run_provenance.py`
- Modify: `backend/infrastructure/persistence/sqlite_run_repository.py`
- Test: `backend/tests/test_model_profiles.py`
- Test: `backend/tests/test_purra_model_protocol.py`
- Test: `backend/tests/test_agent_run_provenance.py`
- Test: `backend/tests/test_purra_sqlite_run_repository.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ModelCapabilitySnapshot:
    schema_version: int
    profile_id: str
    provider_protocol: str
    context_window_tokens: int
    max_output_tokens: int | None
    thinking_token_accounting: ThinkingTokenAccounting
    protocol: ModelProtocolCapabilities

    def digest(self) -> str: ...

@dataclass(frozen=True, slots=True)
class TaskCapabilityRequirements:
    reasoning_mode: ReasoningMode
    tool_calling: FeatureRequirement
    structured_output_level: str
    streaming_required: bool
    cancellation_required: bool

def preflight_capabilities(
    snapshot: ModelCapabilitySnapshot,
    requirements: TaskCapabilityRequirements,
) -> None: ...
```

- [ ] 先写 profile schema 测试：每个注册档案都有稳定 `profile_id`、协议、capability digest；正式任务要求的档案必须有 `max_output_tokens`。
- [ ] 固化当前产品档案已有且被测试覆盖的值：DeepSeek V4 Pro/Flash `393216`，GLM-5.2 与 MiMo V2.5 Pro `131072`。实施时用 Provider 官方文档复核并记录来源；Kimi/MiniMax 若仍无可验证上限，明确标记为 `actionable=False`，不得继承其他模型数字。
- [ ] 新增 capability 组合测试：selectable、always-enabled、unavailable reasoning；required/optional/no tools；JSON object；stream/cancel。
- [ ] 实现 `ModelCapabilitySnapshot`，令 `ModelRequest` 只携带一个不可变快照，不再分别携带易漂移的 output/protocol 字段。
- [ ] 将 `ThinkingTokenAccounting` 与原 `ModelOutputCapabilities` 中仍属客观能力的字段迁入 `model_protocol/capabilities.py`；Task 3 删除的 `output_budget.py` 不再承载能力事实。
- [ ] `RunProvenance` 增加 frozen capability mapping；SQLite Run 增加 immutable `capability_snapshot_json`，与现有 digest 一起写入并受 immutable trigger 保护，确保重启/事故回放能看到当时的真实能力，而不是读取后来更新的 registry。
- [ ] 实现 `preflight_capabilities`；不兼容时抛 `UnsupportedModelFeatureError(code="model_capability_incompatible")`，且测试证明未创建 Run/Artifact。
- [ ] `model_request_from_runtime` 保留用户 reasoning 选择和 `options.max_tokens`，生成 capability snapshot；不得删除或改写用户选择。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_model_profiles.py backend/tests/test_purra_model_protocol.py backend/tests/test_agent_run_provenance.py backend/tests/test_purra_sqlite_run_repository.py -q`
- [ ] Commit: `git commit -m "refactor(model): add versioned capability preflight"`

### Task 3: 用 Invocation Output Limit 取代任务预算公式

**Files:**

- Create: `packages/purra/src/purra/model_protocol/output_limits.py`
- Delete: `packages/purra/src/purra/output_budget.py`
- Delete: `backend/application/output_budget_policies.py`
- Modify: `packages/purra/src/purra/contracts/__init__.py`
- Modify: `packages/purra/src/purra/engine/orchestrator.py`
- Modify: `packages/purra/src/purra/engine/options.py`
- Modify: `packages/purra/src/purra/evaluation/failure_classification.py`
- Modify: `packages/purra/src/purra/model_call_parameters.py`
- Modify: `packages/purra/src/purra/runtime/orchestrator.py`
- Modify: `packages/purra/src/purra/model_execution.py`
- Modify: `packages/purra/src/purra/planner.py`
- Modify: `backend/application/request_mapping.py`
- Modify: `backend/application/screenplay_agent_planner.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_incremental_generation.py`
- Modify: `backend/application/screenplay_structured_call.py`
- Modify: `backend/application/screenplay_tool_calling.py`
- Modify: `backend/application/memory_reranking.py`
- Modify: `backend/application/response_judging.py`
- Modify: `backend/infrastructure/models/model_conversation_summarizer.py`
- Modify: `backend/infrastructure/models/provider_model_gateway.py`
- Modify: `backend/infrastructure/models/profiles/base.py`
- Rewrite: `backend/tests/test_purra_output_budget.py`
- Test: `backend/tests/test_purra_adapters.py`
- Test: `backend/tests/test_agent_composition.py`
- Test: `backend/tests/test_purra_contracts.py`
- Test: `backend/tests/test_purra_engine.py`
- Test: `backend/tests/test_purra_runtime.py`

**Interfaces:**

```python
class InvocationOutputLimitSource(StrEnum):
    USER_OVERRIDE = "user_override"
    MODEL_PROFILE = "model_profile"

@dataclass(frozen=True, slots=True)
class InvocationOutputLimit:
    max_tokens: int
    source: InvocationOutputLimitSource
    profile_max_tokens: int

def resolve_invocation_output_limit(
    snapshot: ModelCapabilitySnapshot,
    explicit_user_override: int | None,
) -> InvocationOutputLimit: ...
```

- [ ] 重写测试，直接断言公式：`explicit_user_override ?? profile.max_output_tokens`；覆盖值大于 profile 上限时报 `model_output_limit_exceeded`。
- [ ] 增加 Adapter 测试：393216 和显式 256000 原样成为 Provider 请求字段；reasoning 开关不能抬高、缩小或替换该值。
- [ ] 删除 `OutputBudgetPolicy`、`ResolvedOutputBudget`、`safety_factor`、`reasoning_reserve_tokens`、任务 `hard_cap` 与 context 百分比计算。
- [ ] 将 `ModelInvocation.output_budget` 替换为 `output_limit`；`AgentCoreRunOptions` 只接受同一 `InvocationOutputLimit`，Context Compiler 使用其 `max_tokens` 作为精确输出保留值。
- [ ] 输入加精确输出上限放不进用户选择的 context window 时，在调用前抛 `model_context_capacity_incompatible`；先压缩可选 context，不得静默缩小 max。
- [ ] 删除 `ModelProfile.internal_output_token_floor()` 与 Gateway 中 `max(maximum, floor)`；Provider Adapter 仅做字段名映射。
- [ ] 更新所有窄调用：用 prompt/schema 约束简洁度，不再用宿主估算值冒充 Provider 上限。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_purra_output_budget.py backend/tests/test_purra_adapters.py backend/tests/test_agent_composition.py backend/tests/test_application_response_judging.py -q`
- [ ] Run: `npm run test:backend`
- [ ] Run: `rg -n "OutputBudgetPolicy|ResolvedOutputBudget|resolve_output_budget|safety_factor|reasoning_reserve_tokens|hard_cap_tokens" packages/purra/src backend/application backend/infrastructure`
- [ ] 断言上一个命令无生产代码命中。
- [ ] Commit: `git commit -m "refactor(model): use profile invocation output limits"`

### Task 4: 归一化 Invocation 终止并删除同请求重试

**Files:**

- Modify: `packages/purra/src/purra/model_protocol/termination.py`
- Modify: `packages/purra/src/purra/runtime/model_round.py`
- Modify: `packages/purra/src/purra/runtime/orchestrator.py`
- Modify: `packages/purra/src/purra/recovery/contracts.py`
- Modify: `packages/purra/src/purra/recovery/disposition.py`
- Modify: `packages/purra/src/purra/model_execution.py`
- Modify: `backend/application/screenplay_structured_call.py`
- Modify: `backend/domains/screenplay_agent/recovery.py`
- Test: `backend/tests/test_purra_runtime.py`
- Test: `backend/tests/test_purra_recovery.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`

**Interfaces:**

```python
class InvocationTermination(StrEnum):
    COMPLETED = "completed"
    LENGTH = "length"
    CANCELED = "canceled"
    TRANSPORT_INTERRUPTED = "transport_interrupted"
    PROTOCOL_INVALID = "protocol_invalid"
    PROVIDER_REJECTED = "provider_rejected"
    TOOL_EFFECT_UNKNOWN = "tool_effect_unknown"

class FailureDisposition(StrEnum):
    RETRY_ATTEMPT = "retry_attempt"
    RESUME_CHECKPOINT = "resume_checkpoint"
    SPLIT_PART = "split_part"
    PAUSE_RECOVERABLE = "pause_recoverable"
    FAIL_PERMANENT = "fail_permanent"
    CANCEL = "cancel"
```

- [ ] 先写 Runtime 测试：reasoning-only、正文、JSON、tool arguments 四种 `length` 均只产生一次相同 request fingerprint。
- [ ] `classify_model_termination` 返回枚举终止事实，不再包含“截断可重试”判断。
- [ ] 删除 `retry_provider_attempt`、`_TRUNCATED_*_RETRY_GUIDANCE` 和为 reasoning-only 截断增加 round 的路径；Runtime 以 `model_output_truncated` 失败当前 Run。
- [ ] 保留 transport/429 等真正 transient 且未提交 effect 的有界 retry；request fingerprint 与 attempt 写入诊断事件。
- [ ] Structured JSON repair 仅处理 finish=completed 但 JSON 协议无效的完整输出；repair 使用原 reasoning mode。`length` 不进入 repair。
- [ ] 删除 `ManagedModelExecutor` 的 reasoning fallback；能力不兼容直接由 preflight 拒绝。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_purra_runtime.py backend/tests/test_purra_recovery.py backend/tests/test_screenplay_agent_rewrite.py -q`
- [ ] Commit: `git commit -m "fix(runtime): never replay truncated model requests"`

### Task 5: 扩展通用 LongTask Part、动态细分与失败范围

**Files:**

- Modify: `packages/purra/src/purra/long_tasks/contracts.py`
- Modify: `packages/purra/src/purra/long_tasks/ports.py`
- Modify: `packages/purra/src/purra/long_tasks/coordinator.py`
- Modify: `packages/purra/src/purra/recovery/contracts.py`
- Modify: `packages/purra/src/purra/recovery/disposition.py`
- Modify: `backend/database/schema.py`
- Modify: `backend/infrastructure/persistence/sqlite_long_task_repository.py`
- Test: `backend/tests/test_purra_long_tasks.py`
- Test: `backend/tests/test_purra_durable_dispatcher.py`

**Interfaces:**

```python
class FailureScope(StrEnum):
    LOCAL = "local"
    SYSTEMIC = "systemic"

@dataclass(frozen=True, slots=True)
class FailureSignal:
    category: FailureCategory
    code: str
    retryable: bool
    scope: FailureScope = FailureScope.LOCAL
    checkpoint_available: bool = False
    part_splittable: bool = False
    effect_state: RecoveryEffectState = RecoveryEffectState.NOT_STARTED

@dataclass(frozen=True, slots=True)
class LongTaskUnitSpec:
    id: str
    semantic_key: str
    position: int
    dependencies: tuple[str, ...]
    parent_unit_id: str | None
    required: bool
    input_ref: str | None
    max_attempts: int
    metadata: Mapping[str, Any]

@dataclass(frozen=True, slots=True)
class LongTaskSplitResult:
    children: tuple[LongTaskUnitSpec, ...]
    replacement_dependency_ids: tuple[str, ...]
```

- [ ] 新增测试：Part id/semantic key 幂等；completed Part 不回退；local blocked Part 不阻止独立 sibling 完成；systemic failure 立即停止新 claim。
- [ ] 新增测试：`length + checkpoint` → resume；`length + splittable` → split；最小 Part 无 checkpoint → `model_task_mode_incompatible`，不循环。
- [ ] SQLite 增加 `semantic_key`、`parent_unit_id`、`required`、`artifact_digest`、`validation_receipt_json`、`failure_json`、`disposition`；为 `(task_id, semantic_key)` 建唯一索引。
- [ ] 增加 `NEEDS_SPLIT` 与 `EXPANDED` Unit 状态。`expand_unit()` 在一个事务内把父 Part 标为非 required/expanded、插入稳定子 Part、重写下游依赖并更新 required totals。
- [ ] Coordinator 在 `SPLIT_PART` 时调用 runner 的 `split_unit(task, unit, error)`，随后 `expand_unit()`；若 splitter 返回空，结算 `model_task_mode_incompatible`。
- [ ] `finalize_if_complete()` 只按 required leaves 判定：全完成→completed；有 local blocked 且无可运行 Part→paused；有永久失败→failed。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_purra_long_tasks.py backend/tests/test_purra_durable_dispatcher.py -q`
- [ ] Commit: `git commit -m "refactor(purra): make durable tasks artifact-part manifests"`

### Task 6: 引入产品级 Screenplay Operation 权威

**Files:**

- Create: `backend/domains/screenplay_agent/operation.py`
- Create: `backend/infrastructure/persistence/sqlite_screenplay_operation_repository.py`
- Modify: `backend/database/screenplay_agent_schema.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`
- Test: `backend/tests/test_screenplay_agent_durable_service.py`
- Test: `backend/tests/test_screenplay_v2_schema.py`
- Test: `backend/tests/test_screenplay_v2_persistence_boundaries.py`

**Interfaces:**

```python
class ScreenplayOperationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"

@dataclass(frozen=True, slots=True)
class ScreenplayOperationRecord:
    id: str
    turn_id: str
    project_id: str
    session_id: int
    status: ScreenplayOperationStatus
    long_task_id: str | None
    target_role: str
    requirements_json: Mapping[str, Any]
    manifest_digest: str
    result_revision_id: str | None
    finalization_receipt_id: str | None
    cancel_receipt_id: str | None
    error: Mapping[str, Any] | None
```

- [ ] 新增 `screenplay_agent_operations` 与 `screenplay_agent_operation_commands`；约束 `turn_id UNIQUE`、`long_task_id UNIQUE`、`finalization_receipt_id UNIQUE`。
- [ ] 增加 project/session 范围内仅一个 active Operation 的 partial unique index；Operation 进入 paused 时仍占用该业务控制权，只有 succeeded/failed/canceled 释放。
- [ ] 写 migration 测试：已有 actionable Turn 按现有 task/status/result 生成 Operation；普通 answer Turn 不生成 Operation；历史 Revision 不被删除。
- [ ] Planner 判定为 create/revise/review 后，先创建 Operation 并持久化 Task Requirements/manifest digest，再 dispatch LongTask；Turn 仅拥有消息与 Operation 引用。
- [ ] snapshot/API 从 Operation 投影任务状态；停止把 Turn 的 `task_id/result_revision_id/error_json` 当权威。旧列暂留作只读迁移来源，边界测试禁止新代码写入。
- [ ] 删除 `screenplay_agent_schema.py` 中“operation-driven Agent 已丢弃”的清理逻辑，改为显式 migration；不恢复旧 Job engine。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_v2_schema.py backend/tests/test_screenplay_v2_persistence_boundaries.py -q`
- [ ] Commit: `git commit -m "refactor(screenplay): add operation control authority"`

### Task 7: 将剧本任务编译为稳定业务 Manifest

**Files:**

- Create: `backend/domains/screenplay_agent/manifest.py`
- Create: `backend/application/screenplay_manifest_compiler.py`
- Replace: `backend/domains/screenplay_agent/recipe_compiler.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/application/screenplay_agent_context.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Delete: `backend/application/screenplay_incremental_generation.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_tool_catalog.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ScreenplayArtifactManifest:
    id: str
    artifact_kind: str
    source_revision_refs: tuple[str, ...]
    assembly_strategy: str
    parts: tuple[ScreenplayPartSpec, ...]
    digest: str

class ScreenplayPartKind(StrEnum):
    EVIDENCE = "evidence"
    DRAFT_SCENE = "draft_scene"
    EPISODE_METADATA = "episode_metadata"
    REVIEW_DIMENSION = "review_dimension"
    VALIDATION = "validation"
    FINAL_RESPONSE = "final_response"
```

- [ ] 写纯编译测试，给定同一 intent/source revisions/scene ids 始终生成相同 Part ids 与 digest。
- [ ] Draft Manifest：每集 evidence；每场一个 `draft:{episode}:{scene_id}`；集 metadata；集 validation。场内按顺序依赖，跨集只依赖上一集 validation，避免复制整稿。
- [ ] Review Manifest：每集一个 immutable input ref；按 `continuity`、`character_arc`、`structure_rhythm`、`dialogue`、`format` 五个维度生成 bounded Part；每集 deterministic aggregate/validation。
- [ ] 其他交付物使用明确文档 section Part；不再以一个无限大的 `generate_deliverable` 作为默认正确性路径。
- [ ] 删除 `generate_episode()`/`generate_review()` 内部 for-loop checkpoints；每个场景/审阅维度成为 LongTask 可见 Unit，失败和恢复由同一 scheduler 处理。
- [ ] `max_parallelism` 由依赖图决定；Review 不再无条件串行，也不在一个 Unit 内捕获全部异常。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_tool_catalog.py -q`
- [ ] Commit: `git commit -m "refactor(screenplay): compile semantic artifact manifests"`

### Task 8: 让 Artifact 成为 Part 内容唯一来源

**Files:**

- Create: `backend/application/screenplay_part_artifacts.py`
- Modify: `backend/infrastructure/screenplay/tools/candidate_artifact.py`
- Modify: `backend/infrastructure/screenplay/candidate_completion_projector.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Delete: `backend/infrastructure/persistence/sqlite_screenplay_task_output_store.py`
- Modify: `backend/database/screenplay_agent_schema.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`
- Test: `backend/tests/test_purra_artifact_continuity.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ValidatedPartArtifactRef:
    artifact_id: str
    run_id: str
    semantic_key: str
    content_digest: str
    validation_receipt: Mapping[str, Any]

class ScreenplayPartArtifactQuery:
    async def require(self, ref: ValidatedPartArtifactRef) -> Mapping[str, Any]: ...
```

- [ ] 新增测试：LongTask Unit 的 `output_ref` 指向 finalized Artifact；DB 中不存在第二份 `output_json/contentText`。
- [ ] Candidate projector 只在 Run completed 且 host validation 通过时 finalize Part Artifact；截断、取消或错误 Run 留下的 open Artifact 不可被 Unit 提交。
- [ ] 删除 `_CandidateValidator` 的固定 `80_000` 字符拒绝；Part 是否过大只能由 Manifest 的业务最小边界与模型终止事实决定，Artifact 存储层不再引入第二个隐藏内容上限。
- [ ] Unit completion 原子写 `artifact_id/digest/validation_receipt`；重复 completion 必须逐字段一致。
- [ ] 依赖读取通过 Artifact ref；evidence 只保存 immutable Revision refs 与 digest，不保存完整项目副本。
- [ ] 删除 `screenplay_agent_task_outputs` 表、store、snapshot join 与删除清理逻辑；迁移只提取仍被活动任务引用的 finalized Artifact ref。无法解析权威 Artifact 的旧活动任务标记为 paused/`artifact_migration_required`，保留已接受 Revision，不复制旧正文冒充新检查点。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_purra_artifact_continuity.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_v2_persistence_boundaries.py -q`
- [ ] Commit: `git commit -m "refactor(screenplay): make artifacts the only part content store"`

### Task 9: 修正 Review 输入、失败传播与完整性

**Files:**

- Modify: `backend/application/screenplay_agent_context.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/domains/screenplay_agent/recovery.py`
- Modify: `backend/domains/screenplay/review_adjudication.py`
- Modify: `src/ScreenplayAgentPage/reviewAdjudicationModel.ts`
- Test: `backend/tests/test_screenplay_project_aggregate.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ReviewEpisodeInputRef:
    reviewed_revision_id: str
    episode_number: int
    scene_part_refs: tuple[str, ...]
    scene_plan_revision_id: str
    content_digest: str

@dataclass(frozen=True, slots=True)
class ReviewEpisodeResult:
    episode_number: int
    reviewed_content_digest: str
    issues: tuple[ReviewIssue, ...]
    verdict: str
    part_receipts: tuple[str, ...]
```

- [ ] 写测试证明 `episode_context(..., draft_revision_id=...)` 能按 immutable Revision ref 读取正文；不允许模型调用“检查候选稿”来推断正文存在性。
- [ ] 写系统性故障测试：profile/protocol/config error 发生后不再 claim 后续集；Operation paused/failed，Review Revision 数量不变。
- [ ] 写局部数据故障测试：独立 sibling 可完成，但失败集保持 blocked；聚合器拒绝发布，直到所有 required episode validations 完成。
- [ ] Review aggregate 只接受 `ReviewEpisodeResult`；异常、failure code、failedEpisodes 不得进入 `issues`、issueCount、criticalIssueCount、verdict 或 contentText。
- [ ] 删除正式 Review schema/UI 中的 `failedEpisodes` 业务字段；失败状态从 Operation Part projection 单独展示“第 N 集审阅失败”。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_project_aggregate.py backend/tests/test_screenplay_agent_rewrite.py -q`
- [ ] Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/reviewAdjudicationModel.test.ts`
- [ ] Commit: `git commit -m "fix(screenplay): keep execution failures out of review findings"`

### Task 10: 原子发布 Candidate、Operation 和唯一最终回答

**Files:**

- Create: `backend/infrastructure/persistence/sqlite_screenplay_operation_finalizer.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_v2_repository.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_operation_repository.py`
- Modify: `backend/application/screenplay_agent_task_executor.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/application/screenplay_structured_call.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py`
- Test: `backend/tests/test_screenplay_agent_durable_service.py`
- Test: `backend/tests/test_screenplay_agent_rewrite.py`
- Test: `backend/tests/test_screenplay_v2_persistence_boundaries.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ScreenplayOperationFinalizationCommand:
    operation_id: str
    expected_manifest_digest: str
    candidate_part_refs: tuple[ValidatedPartArtifactRef, ...]
    final_response_ref: ValidatedPartArtifactRef

@dataclass(frozen=True, slots=True)
class ScreenplayOperationFinalizationReceipt:
    id: str
    operation_id: str
    revision_id: str
    assistant_content_digest: str

class SqliteScreenplayOperationFinalizer:
    async def finalize(
        self, command: ScreenplayOperationFinalizationCommand
    ) -> ScreenplayOperationFinalizationReceipt: ...
```

- [ ] 写故障注入测试：Revision insert、Turn update、Operation update 任一步失败均完整回滚；重放同命令返回同 receipt。
- [ ] 把 V2 candidate publish 拆成可加入 ambient transaction 的内部 repository 操作；finalizer 在一个 `cancellation_linearizable` 事务内校验全部 Part、创建唯一 Revision、写 finalization receipt、Operation succeeded、Turn assistant content。
- [ ] LongTask 完成前只生成并验证短 final-response Artifact，不向对话发送 `delta`；Planner 的 `reply` 只有 ANSWER intent 才成为 Assistant 消息。
- [ ] 删除 `publish-candidate` LongTask Unit 和 service 读取 `compose-final-response` output store 的路径；service 只调用 finalizer。
- [ ] 删除固定“剧本任务已完成，候选稿已生成”与取消/失败 Assistant copy；失败、暂停、取消只更新状态和诊断。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_durable_service.py backend/tests/test_screenplay_agent_rewrite.py backend/tests/test_screenplay_v2_persistence_boundaries.py -q`
- [ ] Commit: `git commit -m "feat(screenplay): atomically finalize operation results"`

### Task 11: 重写取消控制面与终态竞争

**Files:**

- Modify: `backend/routers/screenplay_conversations.py`
- Modify: `backend/application/screenplay_agent_service.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_operation_repository.py`
- Modify: `backend/infrastructure/persistence/sqlite_long_task_repository.py`
- Modify: `packages/purra/src/purra/long_tasks/coordinator.py`
- Modify: `backend/application/run_execution_control.py`
- Test: `backend/tests/test_screenplay_agent_routes.py`
- Test: `backend/tests/test_purra_runtime_cancellation_race.py`
- Test: `backend/tests/test_run_execution_control.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class CancelOperationReceipt:
    id: str
    operation_id: str | None
    turn_id: str
    requested_at: str
    terminal_status: str

async def request_cancel(
    turn_id: str,
    *,
    idempotency_key: str,
) -> CancelOperationReceipt: ...
```

- [ ] 先写 route 测试：key 不可丢弃；同 key 重放同 receipt；不同 key 重复取消仍指向 canonical cancel receipt；key 复用于不同请求返回 409。
- [ ] `request_cancel` 短事务只写 command receipt 与 `cancel_requested_at`；提交后 service 才 signal `_ACTIVE_TASKS`、LongTask 和绑定 Run。
- [ ] Scheduler claim 查询排除 cancel-requested Operation；LongTask cancel 在一个事务内结算 pending/claimed/running Unit，活动半截 Artifact 不提交。
- [ ] CAS 测试 finalization-vs-cancel：finalization 先提交则重复 cancel 返回 succeeded；cancel 先提交则 finalizer 拒绝且状态 canceled；不存在 completed 后反向 canceled。
- [ ] 断开 SSE、页面刷新和 chunk replay 不调用 cancel；重启后 cancel_requested Operation 由 recovery worker 继续结算。
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_screenplay_agent_routes.py backend/tests/test_purra_runtime_cancellation_race.py backend/tests/test_run_execution_control.py -q`
- [ ] Commit: `git commit -m "fix(screenplay): make cancellation durable and idempotent"`

### Task 12: 前端只投影 Operation 事实与唯一产物

**Files:**

- Modify: `src/types.ts`
- Modify: `src/ScreenplayAgentPage/conversationState.ts`
- Modify: `src/ScreenplayAgentPage/conversationState.test.ts`
- Modify: `src/ScreenplayAgentPage/conversationClient.ts`
- Modify: `src/ScreenplayAgentPage/index.tsx`
- Modify: `src/components/AgentConversation/messageVisibility.ts`
- Modify: `src/components/AgentConversation/messageVisibility.test.ts`

**Interfaces:**

```ts
export type ScreenplayOperationStatus =
  | 'queued' | 'running' | 'paused'
  | 'succeeded' | 'failed' | 'canceled'

export interface ScreenplayOperationProjection {
  id: string
  turnId: string
  status: ScreenplayOperationStatus
  parts: ScreenplayOperationPartProjection[]
  resultRevisionId?: string | null
  finalizationReceiptId?: string | null
  error?: { code: string; message: string } | null
}
```

- [ ] 写 state tests：运行中只有 commentary/tool/Part progress；paused/failed/canceled 不产生 Assistant final；succeeded 仅以 finalization receipt 显示一次 final 和一个历史产物面板。
- [ ] artifact panel 条件固定为 `status === 'succeeded' && resultRevisionId && finalizationReceiptId`；不得从 chunk 文案猜完成。
- [ ] 连续工具活动仍按现有消息可见性规则合并；Operation/Part 状态是状态卡，不作为正式 Assistant 消息。
- [ ] 刷新 replay 测试：实时 chunk 与 snapshot 合并后 final、artifact panel 都不重复；取消按钮只在可取消状态可用且展示 command pending。
- [ ] Run: `node --experimental-strip-types --test src/ScreenplayAgentPage/conversationState.test.ts src/components/AgentConversation/messageVisibility.test.ts`
- [ ] Run: `npm run typecheck`
- [ ] Commit: `git commit -m "fix(ui): project authoritative screenplay operation state"`

### Task 13: 换模恢复、usage 汇总与跨模型发布门槛

**Files:**

- Modify: `packages/purra/src/purra/long_tasks/contracts.py`
- Modify: `backend/database/schema.py`
- Modify: `backend/database/screenplay_agent_schema.py`
- Modify: `backend/infrastructure/persistence/sqlite_long_task_repository.py`
- Modify: `backend/infrastructure/persistence/sqlite_screenplay_operation_repository.py`
- Modify: `backend/routers/screenplay_conversations.py`
- Modify: `backend/schemas/screenplay_agent.py`
- Modify: `backend/tests/test_screenplay_agent_routes.py`
- Modify: `src/services/backendApi.ts`
- Modify: `src/ScreenplayAgentPage/conversationClient.ts`
- Create: `backend/tests/support/model_profile_contracts.py`
- Create: `backend/tests/test_registered_model_contracts.py`
- Modify: `backend/tests/test_purra_incident_replay.py`
- Modify: `package.json`
- Modify: `docs/superpowers/specs/2026-08-11-purra-model-agnostic-long-task-execution-design.md`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class OperationUsage:
    invocation_count: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None

@dataclass(frozen=True, slots=True)
class ResumeWithModelCommand:
    operation_id: str
    runtime_profile: Mapping[str, Any]
    expected_operation_revision: int
```

- [x] 为 LongTask 与 Operation 增加独立 `usage_json`/revision 更新合同；按 Run id 幂等累计 input/output/reasoning/invocation count，不塞进可能被 Part progress 覆盖的 metadata，也不据此设置隐藏累计 token hard cap。
- [x] 增加 `POST /screenplay/v2/conversation/operations/{operation_id}/resume`，请求必须带 `Idempotency-Key` 与完整 runtime；前端仅在 paused 状态提交用户当前选择的模型配置。
- [x] 实现显式换模恢复：新 capability snapshot 先通过原 Task Requirements；completed Part refs 保持不变，只领取未完成 Part；不兼容则 Operation 维持 paused，route 返回 409 与 `model_capability_incompatible`。
- [x] 为每个注册 profile/adapter 运行统一 contract：输出字段映射、finish、usage、reasoning、tool argument streaming、cancel；Core 断言不读取 profile/model/provider 名。
- [ ] 项目所有者手工验收 DeepSeek reasoning on/off、GLM-5.2、MiMo V2.5 Pro；结果不纳入仓库自动化门禁。
- [x] 故障 E2E：一次注入 `length` 证明无相同 fingerprint 重试且从 split/checkpoint 继续；一次进程恢复；一次显式换模；一次主动取消。
- [x] Fake Gateway 只用于确定性的合同、状态机和故障回归，不冒充真实 Provider 验收。
- [x] 在 `package.json` 新增 `test:model-contracts`，并把 contract test 纳入 `check:agent-refactor`。
- [x] Run: `.venv/bin/python -m pytest backend/tests/test_registered_model_contracts.py backend/tests/test_purra_incident_replay.py -q`
- [x] Run: `npm run check:agent-refactor`
- [x] Run: `git diff --check`
- [x] 若为测试启动过服务，停止所有本轮进程，并用 `lsof -nP -iTCP:5173 -sTCP:LISTEN` 与后端端口检查确认无监听。（本轮未启动任何服务。）
- [x] 更新设计文档状态，记录模型 profile digest 与真实 E2E 阻塞结果；未将 skip 写成通过。
- [ ] Commit: `git commit -m "test(agent): gate model-agnostic long-task execution"`

## Final Verification Matrix

- [ ] `npm run check:agent-refactor`
- [ ] `npm run check`
- [ ] 项目所有者在仓库门禁之外完成真实 Provider 手工验收。
- [ ] `git diff --check`
- [ ] `rg -n "deepseek|zai|kimi|mimo|screenplay|episode" packages/purra/src/purra`
- [ ] `rg -n "OutputBudgetPolicy|ResolvedOutputBudget|resolve_output_budget|truncated_reasoning_retry|_TRUNCATED_.*RETRY" packages/purra/src backend/application backend/infrastructure`
- [ ] 数据库抽查：一个 succeeded Operation 恰有一个 finalization receipt、一个 Candidate Revision、一个 Assistant final；paused/failed/canceled Operation 三者均为零。
- [ ] Review 抽查：内部 failure 仅出现在 Operation/Part diagnostics，不出现在 Review Issue/正文/verdict。
- [ ] 取消抽查：重复命令返回同 receipt，活动 Run 停止，待领取 Part canceled，端口与 worker 无遗留。

## Implementation Handoff

实施时按 Task 1 → 13 顺序推进。Task 1–5 先稳定通用 PurrA 合同；Task 6–12 再迁移剧本产品；Task 13 最后执行跨模型门槛。任何任务的失败测试没有证明旧行为错误，或通过测试无法证明新不变量时，暂停并修正规格/测试，不得继续叠加补丁。
