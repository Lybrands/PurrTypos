# PurrA 执行可靠性与剧本任务恢复设计（已被替代）

> 日期：2026-08-10  
> 状态：已被 `2026-08-11-purra-model-agnostic-long-task-execution-design.md` 替代，请勿继续作为实施依据。
> 范围：PurrA 通用执行协议、模型能力协商、持久任务恢复、剧本 Agent 执行配方与对话最终输出  
> 约束：不参考或复制外部 Agent 框架；所有结论来自当前代码、持久化事故记录和 PurrTypos 产品不变量。

## 1. 背景与问题定义

现有剧本 Agent 已完成 Conversation、Turn、Operation、Run、Long Task、Artifact 和 Revision 的分层，但真实执行仍持续出现以下错误：

- `tool_execution_failed`
- `invalid_tool_results`
- `invalid_tool_arguments_json`
- `tool_call_truncated`
- `model_output_truncated`
- `max_model_rounds`
- `provider_bad_request`

最近被用户指出的九个 Run 全部失败，而且都跳过了执行规划。与此同时，同一时间段存在 53 个成功的剧本任务子 Run 和 9 个失败子 Run。这说明产品层面的“百分百失败”并不是所有底层调用都失败，而是当前系统会把一个子 Run 的失败放大为整个 Operation/Turn 的失败。

当前可靠性缺陷由三部分共同构成：

1. 正式任务的单个模型 Unit 内仍是开放工具循环。模型同时决定读取、证据是否足够、生成、提交和停止时机。
2. Run 级恢复与 Long Task 级恢复没有形成统一的失败处置契约。可恢复错误耗尽一次 Run 预算后，会被上抛并终止整个业务任务。
3. 现有自动化主要验证确定性宿主路径和 Fake Gateway，未把真实模型端到端、历史事故重放和模式兼容作为发布门槛。

## 2. 目标

本次改造必须实现以下结果：

1. 可恢复的模型、工具和协议错误不得直接终止整个 Operation。
2. 已完成 Unit、已确认工具证据和已写入 Artifact 不得因为后续失败而丢失或重复生成。
3. 用户选择的思考模式是一次主任务的不可变意图，Runtime 不得静默切换。
4. Provider 和模型差异通过能力契约表达，Core 不出现模型名或 Provider 名分支。
5. 中间模型正文、工具前 commentary 和最终回答使用不同事件语义；正式回答只在整个业务任务完成后提交一次。
6. 失败恢复以持久化检查点为起点，不以不断增长的同一个模型会话为起点。
7. 历史九个事故样本及其七类错误、故障注入和真实模型端到端成为完成门槛。

## 3. 非目标

本次改造不承诺以下事项：

- 不承诺外部 Provider 永不返回鉴权、余额、网络或服务错误。
- 不通过无限提高 `max_output_tokens` 或模型轮次掩盖编排问题。
- 不把剧本阶段、工具名、Revision 或 Scene 等业务语义写入 PurrA。
- 不建立第二套长期并存的 Agent Runtime 或生产 fallback。
- 不恢复旧剧本 Conversation、旧 SSE reducer 或旧前端 JSON 聚合路径。
- 不把完整私有 chain-of-thought 当作唯一执行日志。

## 4. 方案比较

### 4.1 方案 A：继续扩展当前 Runtime 的错误分支

做法：针对每个错误码增加重试、fallback 或提示。

优点：改动小，短期容易让某个事故消失。

缺点：错误码继续增长；Run 与业务任务恢复仍然割裂；无法解决失败放大；会重复此前“修一个、再爆一个”的路径。

结论：拒绝。

### 4.2 方案 B：一次性重写整个 PurrA Runtime 和剧本 Agent

做法：删除现有 Runtime、Long Task、工具循环和恢复实现，重新建立一套完整系统。

优点：理论上边界最干净。

缺点：当前工作区已经包含一次大规模未提交迁移；一次性替换会失去可归因性；现有 lease、幂等、Artifact、投影和 SSE 不变量容易回归。

结论：拒绝。

### 4.3 方案 C：契约先行、单路径逐步替换

做法：先把历史事故固化成失败测试，再建立能力、失败处置和检查点契约；随后沿唯一生产路径逐步替换 Run、Long Task 和剧本 Recipe 的行为。每完成一个边界就删除相应旧分支，不保留长期双轨。

优点：每一步都可证伪、可归因；保留已经正确的持久化和业务边界；允许删除当前未提交代码中与新不变量冲突的实现。

缺点：需要先补齐事故测试和数据迁移，不能立刻用一次大改得到表面结果。

结论：采用此方案。

## 5. 核心不变量

### 5.1 状态所有权

- PurrA 拥有 Run、模型尝试、通用 Recipe/Unit、工具 effect、恢复决定和通用事件。
- 剧本 Application 拥有 Turn、Operation、剧本 Recipe 编译和业务完成投影。
- 剧本 Domain 拥有 Candidate/Revision 校验与发布规则。
- Provider Adapter 只负责把通用模型协议转换为具体传输协议。
- 前端只投影持久化事实，不推导业务终态。

### 5.2 失败不等于任务终止

- 单个 Run 是一次不可变的执行尝试，可以终态为 `failed`。
- Run 失败后，所属 Unit 根据失败处置进入 `waiting_retry`、`blocked` 或 `failed`。
- 可恢复错误耗尽自动预算后进入 `blocked`，Long Task/Operation 映射为 `paused`，不得映射为不可恢复失败。
- 只有永久外部错误、确定性业务不变量失败、用户取消或显式策略拒绝才能使 Unit 和任务进入终态失败/取消。

### 5.3 已完成事实不可回退

- `completed` Unit 不得回到未完成状态。
- 已提交工具 effect 不得因为模型重试重复执行。
- 已写入且校验通过的 Artifact 必须通过引用复用。
- 重新执行只允许从最近一个未完成检查点开始。

## 6. 通用协议设计

### 6.1 不可变运行意图

新增通用 `RunExecutionIntent`，至少包含：

```text
RunExecutionIntent
├── requested_reasoning_mode: enabled | disabled
├── output_contract
├── tool_protocol_contract
├── recovery_policy_id
└── capability_snapshot_digest
```

规则：

- `requested_reasoning_mode` 来自前端提交时的配置，并持久化为 Run provenance。
- 主任务的所有模型尝试必须保持该值。
- Runtime 不得在截断后把 `enabled` 改为 `disabled`。
- 宿主内部 Planner/Judge 等辅助调用是独立 Invocation，必须显式声明自己的模式，且不能修改主任务意图。

### 6.2 模型协议能力

新增 `ModelProtocolCapabilities`：

```text
ModelProtocolCapabilities
├── reasoning_control: selectable | always_enabled | unavailable
├── reasoning_replay: required | forbidden | ignored
├── tool_calling: supported | unavailable
├── required_tool_choice: supported | unavailable
├── parallel_tool_calls: supported | unavailable
├── assistant_content_with_tool_calls: required | optional | forbidden
├── json_schema_level
├── stream_finish_semantics
└── usage_semantics
```

规则：

- Core 只读取能力值，不读取 Provider 或模型名称。
- Adapter/Profile 负责声明静态能力；运行期只允许记录经过明确探测的能力变化。
- 用户配置与能力不兼容时，在创建有副作用的 Run 前失败。
- 未知能力采用保守传输：不发送未经声明支持的扩展字段。
- 能力快照或其稳定摘要必须随 Run 持久化，确保事故可重放。

### 6.3 通用 Recipe 与领域阶段

PurrA 不硬编码 `GATHER/SYNTHESIZE/COMMIT`。PurrA 只执行通用 `ExecutionRecipeStep` 和依赖关系。

每个 Step 增加通用执行契约：

```text
StepExecutionContract
├── effect_class: read_only | idempotent_write | non_repeatable_write
├── completion_evidence
├── retry_policy
├── checkpoint_policy
└── allowed_executor_capabilities
```

剧本 Application 可以把一个正式生成 Unit 编译为以下领域步骤：

```text
collect_evidence
    ↓
generate_candidate
    ↓
validate_candidate
    ↓
publish_candidate
```

这些名称和业务输入只存在于剧本侧。PurrA 只看到 executor、依赖、effect class、完成凭证和状态转换。

### 6.4 模型阶段边界

正式剧本生成不得继续由一个开放模型会话同时完成读取和超长候选提交。

- `collect_evidence`：只开放 READ 工具；输出经验证的证据引用/receipt，不写候选正文。
- `generate_candidate`：消费持久化证据快照；只允许候选写入工具，不再开放 READ 工具。
- `validate_candidate`：确定性宿主校验，不依赖模型口头确认。
- `publish_candidate`：只根据校验通过的 Artifact 引用发布 Revision。

模型仍可在读取阶段选择需要的资料，但不能决定业务提交顺序，也不能用一句自然语言把任务标记完成。

## 7. 失败处置模型

新增通用失败分类和处置，不直接用 Provider 错误码决定业务终态：

```text
FailureDisposition
├── retry_attempt       # 同一 Unit 创建新的 Run 尝试
├── resume_checkpoint   # 从持久化阶段检查点继续
├── pause_recoverable   # 需要配置、预算或用户重新继续
├── fail_permanent      # 确定无法继续
└── cancel
```

分类输入包括：

- failure category；
- 当前 Step 和 effect class；
- effect 是否已经提交；
- 当前自动尝试预算；
- 用户取消状态；
- 模型能力快照；
- 是否已有有效 output/artifact/checkpoint。

恢复预算只控制“自动继续多少次”，不能把“预算耗尽”解释成业务结果永久失败。预算耗尽后进入 `pause_recoverable`。

## 8. 历史错误的目标行为

| 历史错误 | 当前问题 | 新行为 |
|---|---|---|
| `tool_execution_failed` | 一个工具异常终止 Unit/Task | 根据工具 effect 和错误分类重试当前 Step；不确定 effect 时暂停并保留证据 |
| `invalid_tool_results` | 非法结果聚合为 Run 失败 | 在工具边界校验；无副作用时修复/重试，确定性授权错误暂停并暴露诊断 |
| `invalid_tool_arguments_json` | 损坏 JSON 直接消耗整个 Unit | 视为无副作用的模型尝试失败，仅重做当前生成/提交 Step |
| `tool_call_truncated` | 候选参数截断导致整个任务失败 | 保留证据检查点，以相同思考模式重做候选生成；不得重读已确认资料 |
| `model_output_truncated` | 在同一长会话中继续或静默关闭思考 | 保持模式不变，从阶段检查点创建新 Run；有有效 Artifact 时直接进入校验 |
| `max_model_rounds` | 轮次预算成为任务终态 | 当前 Step 进入可恢复暂停；完成的 Unit 不回退，用户继续时增加尝试预算 |
| `provider_bad_request` | 执行中才发现协议不兼容 | 能力预检尽量前置；无副作用时暂停配置；绝不静默改变用户模式 |

## 9. Long Task 状态改造

现有 `LongTaskUnitStatus` 需要能够表达“自动预算耗尽但仍可恢复”。目标状态：

```text
pending → claimed → running
                    ├── waiting_retry → claimed
                    ├── blocked → pending（显式 resume）
                    ├── completed
                    ├── failed（永久）
                    └── canceled
```

Long Task 聚合规则：

- 所有 Unit 完成才是 `completed`。
- 存在 blocked Unit 且没有运行中 Unit时为 `paused`。
- 只有存在永久 failed Unit 才是 `failed`。
- 独立 Unit 可以继续执行；依赖 blocked/failed Unit 的下游 Unit不得执行。
- Resume 只重置 blocked Unit，并增加明确记录的尝试预算；不得重置 completed Unit。

现有 `output_ref`、`run_id`、`attempt` 和 `max_attempts` 继续作为基础，但需要补充失败分类、处置、检查点引用和最近能力快照。

## 10. 输出协议与前端投影

### 10.1 事件职责

- `MODEL_REASONING_DELTA`：仅属于开启思考的模型过程；进入可展开思考区或受控诊断，不得成为正式回答。
- `MODEL_CONTENT_DELTA`：模型在工具前后主动输出的公开说明；在未完成阶段进入执行过程区。
- `TOOL_*`：真实工具调用、完成和安全摘要；进入执行过程区。
- `PHASE_PROGRESS`：由持久化 Recipe/Unit 状态投影；进入计划/进度区。
- `ASSISTANT_FINAL`：由剧本 Application 在 Operation 完成并取得 Revision 后生成；同一 Turn 只允许一次。

### 10.2 思考模式

- 开启：可展示 `MODEL_REASONING_DELTA`，同时保留公开 commentary 和工具记录。
- 关闭：不展示思考流，但仍展示公开 commentary、工具记录和阶段进度。
- 无论开启或关闭，中间 `MODEL_CONTENT_DELTA` 都不能提前提交为正式 Assistant Message。
- Provider 在关闭模式下意外返回 reasoning 时，记录协议诊断，不把它混入正式回答。

### 10.3 最终输出

子 Run 的 Run-level final response 可以作为执行事实持久化，但不得直接投影成产品 Assistant 最终回答。产品最终回答只由 Operation 完成投影产生，并引用已发布 Candidate Revision。

## 11. 持久化与迁移

迁移必须是可回滚的结构迁移，不删除 Project、Revision、Artifact、Book、Outline 或其他创作数据。

需要持久化：

- Run 请求的思考模式；
- 能力快照摘要和有效传输模式；
- Unit failure category/disposition；
- Unit checkpoint/output/artifact 引用；
- 自动尝试和显式 resume 增加的预算；
- Operation 级唯一 finalization receipt。

数据库约束：

- Run provenance 和 reasoning mode 创建后不可修改；
- completed Unit 不允许被更新为非 completed；
- 同一 Turn 最多一个 Operation finalization receipt；
- 同一候选 effect 的幂等键唯一；
- Resume 和重放不得重复发布 Revision。

## 12. 实施顺序

### Phase 1：事故固化与行为基线

- 在修改现有实现前建立明确标记为非稳定版本的本地代码检查点，或保存等价的可恢复差异快照；不得把本设计和后续实现混入改造前快照。
- 从九个失败 Run 提取不含用户正文的事件序列 fixture。
- 为七类错误建立失败测试，证明当前实现会把可恢复错误升级为任务失败。
- 建立“已完成 Unit/Artifact 不得重做”的 characterization tests。

### Phase 2：能力与运行意图

- 引入 `RunExecutionIntent` 和 `ModelProtocolCapabilities`。
- 持久化思考模式和能力摘要。
- 删除主任务 reasoning-only truncation 后自动改为 disabled 的行为。
- 为能力不兼容建立副作用前预检。

### Phase 3：统一失败处置

- 引入 `FailureDisposition`。
- 让 Run recovery 和 Long Task unit settlement 使用同一份处置结果。
- 增加 waiting/blocked 状态和恢复预算语义。
- 修改剧本 Turn：可恢复暂停不得调用 `fail_task`。

### Phase 4：剧本 Recipe 拆分

- 将正式生成拆为证据、生成、校验、发布步骤。
- 限制每个阶段可见工具和 effect class。
- 复用已完成 output_ref、source receipt 和 Artifact。

### Phase 5：输出收口

- 区分 reasoning、公开 commentary、工具活动、进度和最终回答。
- 缓冲子 Run 正文，不直接写入正式 Assistant Turn。
- 只在 Operation finalization receipt 成功后发布一次最终回答。

### Phase 6：删除旧行为并验证

- 删除 reasoning 静默 fallback、可恢复错误直接 fail_task 和开放式正式生成路径。
- 运行架构门禁、完整前后端测试、事故重放、故障注入和真实模型端到端。
- 测试启动的所有服务必须在交付前关闭并确认端口无监听。

## 13. 测试与完成门槛

### 13.1 确定性测试

- 七类历史错误全部有回归测试。
- 九个事故事件 fixture 全部得到预期处置。
- 思考开启/关闭在整个主任务中保持不变。
- 工具 effect 为 committed/unknown 时不会危险重试。
- completed Unit、output_ref 和 Artifact 在 resume 后保持不变。
- blocked Unit resume 后只创建新的 Run attempt。
- 最终回答只出现一次且只在 Revision 发布后出现。
- SSE 重放与实时路径得到相同的最终界面状态。

### 13.2 能力组合测试

至少覆盖：

- reasoning selectable / unavailable / always enabled；
- reasoning replay required / forbidden；
- required tool choice supported / unavailable；
- parallel tool calls supported / unavailable；
- malformed JSON、partial tool call、reasoning-only length、stream interruption；
- Provider 在副作用前后失败。

测试按能力组合组织，不按模型名称组织。

### 13.3 真实执行门槛

在声称完成前，至少使用当前配置中的：

- 一个支持用户控制思考模式的模型；
- 一个协议能力不同的模型；

分别完成一个最小咨询 Turn 和一个正式候选任务，并验证：

- 工具调用和公开 commentary 正确流式展示；
- 思考模式与前端选择一致；
- 正文不泄漏到执行过程；
- Candidate/Revision 正确发布；
- 主动制造一次截断或中断后可以从检查点继续；
- 刷新和重放不改变状态；
- 已完成 Unit 不重复执行。

### 13.4 发布判定

不得以“测试数量很多”作为完成依据。必须同时满足：

1. `npm run check:agent-refactor`；
2. 后端完整测试；
3. 前端单测和 TypeScript typecheck；
4. `git diff --check`；
5. 历史事故重放；
6. 能力组合故障注入；
7. 两类能力模型的真实端到端；
8. 没有新增模型名/Provider 名 Core 分支；
9. 没有可恢复错误直接导致 Operation 永久失败；
10. 没有遗留本轮启动的服务进程。

## 14. 风险控制

- 当前未提交代码可以被本轮改造替换，但每次修改只围绕一个契约或状态转换，并先有失败测试。
- 不同时修改协议、持久化、前端和领域逻辑后才统一测试；每个阶段都建立独立通过门槛。
- 第三个未经证实的修复尝试出现时停止实现，返回事故证据和状态所有权复核。
- 任何真实执行失败先查询 Run、事件、Unit、Artifact 和 Revision，再决定下一步，不根据错误文案猜测。

## 15. 完成定义

本次改造完成不意味着外部系统永不报错，而意味着：

- 可恢复错误不会丢失工作，也不会直接终止整个任务；
- 永久错误能够在副作用前被识别，或在副作用后安全暂停；
- 模型差异只改变能力协商结果，不改变业务生命周期；
- 用户最终只看到一次与真实持久化结果一致的正式回答；
- 真实模型执行、历史事故和故障注入共同证明上述行为。
