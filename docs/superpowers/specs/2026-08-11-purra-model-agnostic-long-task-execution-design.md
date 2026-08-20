# PurrA 模型无关长任务执行与恢复设计

> 日期：2026-08-11
> 状态：已实现；自动化门禁仅覆盖离线合同、协议与状态机，真实 Provider 验证由项目所有者手工执行
> 取代：`2026-08-10-purra-execution-reliability-design.md`
> 范围：PurrA 通用模型协议、长任务语义分片、持久化恢复、失败与取消收口、剧本审阅配方、最终输出协议
> 依据：当前代码、已持久化的真实失败 Run、PurrA/剧本分层章程，以及本轮已确认的产品约束。

## 0. 实施与发布门禁状态

2026-08-11 已完成本设计对应的 Core、Profile、Operation、LongTask、Artifact、取消、恢复与前端投影改造。自动化门禁范围如下：

- 本地合同门禁已通过：注册 profile 统一请求/finish/usage/reasoning/tool argument stream/cancel 合同；Core 厂商名隔离；显式换模恢复；Run 级 usage 幂等汇总；Candidate/Revision/Assistant final 原子收口；暂停与取消恢复测试。
- 仓库不维护或执行付费真实 Provider E2E；真实模型、账号、网络与额度验证由项目所有者在仓库门禁之外手工完成。

本次 1M 上下文能力快照 digest：

| Profile | Digest | Actionable | 单次输出上限 |
|---|---|---:|---:|
| `zai:glm-5.2` | `d1761ba0bc69e63c9a3479857589d76cb3692bd373b7dc8549b141225756bc8d` | 是 | 131072 |
| `deepseek:deepseek-v4-pro` | `06503ce0b4ccc6a87c15bd6f80363a63373b9807d30e4d07c88ab0eb2ee42869` | 是 | 393216 |
| `deepseek:deepseek-v4-flash` | `f5f9a1882a1d47c99bd8231fcd7f3a45990d5a85c88906b8f069fb5e4fdddbf8` | 是 | 393216 |
| `moonshot:kimi-k3` | `0eee4cf8339f19cd19fe9e2123623c0a54000295dd1d48fda20064a2a39e3ae3` | 否 | 未验证 |
| `moonshot:kimi-k2.6` | `c500cc565a1555efc726faf85ba63950d766f60041a80ad7c76a459ce9df828c` | 否 | 未验证 |
| `minimax:MiniMax-M3` | `d6e6cf85a2af4f807ef5c64b4c519f9f7db87a976971b1ae0701bd44d993b9b8` | 否 | 未验证 |
| `mimo:mimo-v2.5-pro` | `e4bba7ae9a81c852497424954e9ca24c335c428e35a92c6f77ccb78c0ebf38f1` | 是 | 131072 |

## 1. 决策摘要

本轮不再把问题定义为“DeepSeek 输出容易超限”，而是把它定义为：当前系统错误地把一次模型调用的输出上限当成了整个业务任务的完成预算，并在 `length` 后重放相同请求；同时，分集失败、任务完成、最终输出和取消没有共享一套持久化状态协议。

采用以下设计：

1. 模型档案声明模型的客观能力和官方限制；PurrA Core 不读取模型名或 Provider 名。
2. `ModelInvocation.max_tokens` 直接使用用户显式覆盖值，或模型档案的单次最大输出值；不再由任务估算公式缩小。
3. 单次模型调用不是完整长任务。业务层先生成稳定的语义 Part Manifest，每次调用只完成一个有边界的 Part，并立即持久化 Artifact 检查点。
4. `finish_reason=length` 不再触发原样重试。已有有效检查点则继续未完成 Part；当前 Part 可拆则生成子 Part；最小 Part 仍无法完成则明确判定模型与任务模式不兼容。
5. Review 等多 Unit 任务必须处理所有必需 Unit。内部执行失败不得写成审阅意见，也不得用不完整结果发布 Candidate Revision。
6. Long Task 只在所有必需 Part 完成后成功；最终 Assistant 输出也只在 Operation 真正成功后写入一次。
7. 取消是持久化命令，不依赖关闭 SSE 或等待网络调用结束；它必须从 Turn 传播到 Operation、Long Task、Run 和待执行 Unit。
8. 实际累计 token 只用于观测、费用与显式配额，不再充当一个隐藏的默认 160K/256K/384K 任务硬上限。
9. DeepSeek 只是首个能力档案样例。所有运行、分片、恢复和取消行为必须对其他模型保持同一语义。

## 2. 现状与真实根因

当前实现存在以下相互放大的缺陷：

- Provider 的单次请求 `max_tokens` 会被任务预算公式压缩，而不是优先使用模型档案中的真实最大输出能力。
- `length` 被归类为普通可重试错误；reasoning-only 截断后只递增 attempt，消息、任务范围和输出策略均不改变。
- 多集审阅遇到一集失败后会继续执行后续集，并把“正文不可读”“工具缺失”等系统失败写入正式审阅内容。
- 子 Run 可以分别完成或失败，但 Operation 没有严格以“所有必需语义 Part 完成”作为成功条件。
- Run 级文本能够在业务任务完成前进入对话区，造成“正式输出—继续执行—再次输出”的交错。
- 取消链路包含事务嵌套和活动 worker 竞争，用户命令不能可靠成为唯一终止事实。

因此，继续增加错误码补丁、重试次数或 DeepSeek 特殊分支不会解决根因。

## 3. 目标

### 3.1 必须实现

- 同一套长任务协议适配 DeepSeek、智谱、Kimi、MiMo 以及后续注册模型。
- 一个业务产物可以大于任意一次模型调用的最大输出，而不要求单次返回完整正文。
- 单个 Part 失败时不丢失已完成 Part；恢复从持久化检查点继续。
- `length`、网络中断、协议错误、工具错误、用户取消拥有不同且确定的状态转换。
- 内部失败不污染正式业务 Artifact。
- 只有真实完整的 Candidate/Revision 才能使 Operation 成功。
- 用户选择的思考模式在任务内保持不变，不被 Runtime 静默开关。
- 实时订阅与刷新重放得到相同状态、相同最终回答和相同产物入口。

### 3.2 非目标

- 不承诺所有模型具备相同能力。无工具调用能力的模型不能被伪装成工具模型。
- 不承诺外部 Provider 永不返回余额、鉴权、限流或服务错误。
- 不依赖任何 Provider 的 Beta 续写接口作为长任务正确性的基础。
- 不通过无限轮次、无限调用或静默换模掩盖不可完成任务。
- 不把剧集、场景、章节、人物或 Revision 语义写入 PurrA Core。
- 不保留新旧两套生产 Runtime 长期并行。
- 不把私有思维链当作任务进度、正式输出或恢复依据。

## 4. 方案选择

### 4.1 方案 A：提高现有任务预算并继续原样重试

将 10.6K、40K 等任务上限提高到 256K 或 384K，同时增加重试次数。

问题：单次输出仍可能达到上限；原样重试不会增加完成概率；大于单次上限的任务仍然不可能完成；失败和取消状态仍未解决。

结论：拒绝。

### 4.2 方案 B：模型能力上限 + 语义分片 + 持久化恢复

模型档案提供单次真实上限；业务层把产物编译为可验证 Part；PurrA 按 Part 调用、检查点和恢复；宿主确定性组装完整产物。

优点：不依赖特定模型的续写功能；任务可超过单次输出限制；恢复单位清晰；可以证明哪些内容已经完成。

代价：需要重构预算、终止分类、Part 状态、审阅配方、取消和最终输出收口。

结论：采用。

### 4.3 方案 C：保存原始截断文本并让下一次自由续写

每次截断后把原始输出作为下一次 prompt，要求“从上次继续”。

问题：不同模型的续写协议和重复边界不一致；损坏的 JSON/工具调用无法安全继续；上下文不断膨胀；宿主无法证明重复、遗漏和顺序正确。

结论：仅可作为某些纯文本 Part 内部的显式能力优化，不能成为正确性基础。

## 5. 分层与所有权

| 层级 | 拥有 | 禁止 |
|---|---|---|
| Model Profile | 模型上下文、单次输出、思考、工具、结构化输出、流和 usage 能力 | 任务重试、剧本分片、Operation 状态 |
| Provider Adapter | 请求字段映射、流事件归一、finish/usage/error/cancel 转换 | 根据业务内容决定预算、分片或成功 |
| PurrA Core | Invocation、Run、通用 Part/Step 状态、检查点、失败处置、取消、usage | 读取模型名、识别剧集/场景、发布 Revision |
| Product Application | Task Requirements、语义 Manifest、业务依赖、组装策略、Operation 投影 | 实现 Provider 协议或 Core Run 状态机 |
| Product Domain | Candidate/Revision 校验、完整性与接受规则 | 模型调用、SSE、数据库事务编排 |
| Frontend | 用户配置、命令提交、持久化事实投影 | 推导任务成功、用断开连接代替取消 |

硬性边界：

- `packages/purra` 中不得出现 `deepseek`、`zai`、`kimi`、`mimo`、`screenplay`、`episode` 等产品或厂商分支。
- 厂商差异只能存在于 Profile 和 Infrastructure Adapter。
- 剧本如何拆集、小说如何拆章，只能由各自 Product Application 的 Manifest Compiler 决定。
- Core 可以传输不透明的 Part key 和 Artifact ref，但不得解释其业务含义。

## 6. 模型无关能力协议

### 6.1 Model Capability Profile

每个可选择模型必须解析成版本化能力档案：

```text
ModelCapabilityProfile
├── profile_id
├── model_id
├── provider_protocol
├── context_window_tokens
├── max_output_tokens
├── reasoning
│   ├── control: selectable | always_enabled | unavailable
│   ├── accounting: included | separate | unavailable
│   └── replay: required | forbidden | ignored
├── tool_calling
│   ├── availability
│   ├── required_choice
│   ├── parallel_calls
│   └── argument_streaming
├── structured_output
├── stream_finish_semantics
├── cancellation_semantics
└── usage_semantics
```

规则：

- DeepSeek V4 档案中的 `max_output_tokens` 为 393216；该数字只表示一次 Invocation 的请求上限。
- 其他模型使用各自档案声明值，不继承 DeepSeek 数字。
- 未登记的自定义模型必须由用户配置或注册表明确提供必要能力；不得根据模型名猜测。
- Profile 变更产生新的稳定摘要。每次 Invocation 持久化所用 Profile id、模型 id、协议和能力摘要。
- 能力档案描述事实，不包含业务任务估算。

### 6.2 Task Requirements 与预检

业务配方声明能力需求，而不是声明模型名称：

```text
TaskCapabilityRequirements
├── reasoning_mode: enabled | disabled
├── tool_calling: required | optional | none
├── structured_output_level
├── streaming_required
└── cancellation_required
```

创建有副作用的 Run 前完成预检：

- 能力满足：生成不可变 Invocation。
- 用户选择与模型能力冲突：返回明确的 `model_capability_incompatible`，不创建副作用 Run。
- 工具能力不足：不退化为让模型口头假装执行工具。
- Runtime 不得静默关闭思考、静默换模或删除输出契约。

“无缝切换模型”指：满足同一 Task Requirements 的模型无需修改 PurrA 或业务状态机即可执行。它不表示缺少必要能力的模型可以无条件完成该任务。

### 6.3 模型切换语义

- 每个 Invocation 使用一个不可变模型绑定。
- 活动 Invocation 期间禁止静默切换模型。
- 用户显式更换模型并恢复任务时，新模型先通过相同 Task Requirements 预检，然后从下一个未完成 Part 继续。
- 已完成 Part 和 Artifact 不因换模重做；新旧 Invocation 分别保留自己的能力摘要和 usage。
- 若新模型不兼容，任务保持 `paused`，不损坏已有产物。

## 7. 输出上限与预算语义

### 7.1 单次 Invocation 上限

```text
ModelInvocation.max_tokens =
    explicit_user_override
    ?? model_capability_profile.max_output_tokens
```

Provider Adapter 只做字段映射，例如 `max_tokens` 或 `max_completion_tokens`，不得再次按比例缩小。

删除以下影响 Provider 上限的逻辑：

- `base_tokens`
- `safety_factor`
- `reasoning_reserve_tokens`
- 任务 `hard_cap`
- 按上下文窗口百分比计算的输出保留区
- “最大输出为上下文一半”等没有模型协议依据的限制

上下文装配仍必须在调用前验证：

```text
actual_input_tokens + requested_max_output_tokens <= context_window_tokens
```

若不满足，Context Compiler 应减少可选输入、改用检索摘要或缩小当前语义 Part；不得用隐藏百分比把输出上限改成另一个猜测值。若必需输入在完成这些处理后仍无法与所请求输出共同放入窗口，则在调用前返回明确的不兼容结果。

### 7.2 用户显式覆盖

- 用户把某次调用限制为 256K 时，Provider 收到 256K。
- 覆盖值不得超过模型档案的官方单次输出上限。
- 覆盖值只约束一次 Invocation，不成为 Long Task 的累计总预算。

### 7.3 累计 usage

Long Task/Operation 累计记录 input、output、reasoning tokens、Invocation 数量、Provider usage 差异和可得时的费用。

累计 usage 的用途只有观测、费用展示、告警和用户/组织显式配额。系统不设置隐藏的默认 160K、256K 或 384K Operation 总上限。

## 8. 语义 Manifest 与 Part 执行

### 8.1 Manifest

正式任务开始前，Product Application 生成持久化 Manifest：

```text
ArtifactManifest
├── manifest_id
├── artifact_kind
├── source_revision_refs
├── assembly_strategy
└── required_parts[]
    ├── part_id
    ├── semantic_key
    ├── parent_part_id
    ├── dependencies[]
    ├── status
    ├── attempt
    ├── artifact_ref
    ├── content_digest
    ├── validation_receipt
    └── failure
```

Part id 和 semantic key 必须稳定；刷新、重启、重试和换模不得重新生成另一套身份。

### 8.2 产品分片

- 剧本正文：剧集 → 场景。
- 场景规划：剧集 → 场景批次。
- 剧本审阅：剧集 → 审阅维度或场景批次。
- 小说知识：人物、背景、世界观等独立知识文档。
- 小说正文可以使用自己的章节分片配方，不复用知识编辑 UI。

PurrA 只执行 Part 依赖图，不理解上述名词。

### 8.3 每个 Part 的提交边界

一次 Part 执行：

1. 读取持久化来源引用和已验证依赖，不复制整个项目到 prompt。
2. 创建 ModelInvocation。
3. 生成当前 Part 的结构化结果或 Artifact 内容。
4. 在宿主侧验证 schema、semantic key、完整性和来源 receipt。
5. 原子写入 Artifact ref、digest、validation receipt 和 `completed` 状态。
6. 调度下一个可运行 Part。

模型不负责回显并组装完整长文档。所有 Part 完成后，由宿主按 Manifest 确定性组装 Candidate Revision。

### 8.4 动态细分

若一个 Part 达到单次输出上限：

- Part 有业务允许的子边界：父 Part 进入 `needs_split`，Manifest Compiler 生成稳定子 Part。
- 已有通过验证的子 Artifact：保留已有内容，只执行缺失子 Part。
- 当前 Part 已是业务定义的最小单位且没有有效检查点：失败为 `model_task_mode_incompatible`。

动态细分必须由业务策略生成，不能由模型随意改变 Artifact 结构。

## 9. `length` 与失败处置

### 9.1 `length` 不是普通重试

Provider Adapter 将厂商结果归一成：

```text
InvocationTermination
├── completed
├── length
├── canceled
├── transport_interrupted
├── protocol_invalid
├── provider_rejected
└── tool_effect_unknown
```

收到 `length` 时，禁止相同 messages/Part/max_tokens 原样重试、猜测预算、静默关闭思考、执行截断 JSON/工具调用，或把半截文本当成已完成 Part。

目标处置：

```text
有效检查点存在 → resume_remaining_parts
当前 Part 可细分 → split_current_part
最小 Part 且无检查点 → fail_model_task_mode_incompatible
```

仅当 Profile 明确声明安全续写能力、Part 是纯文本且宿主能验证拼接边界时，Adapter 可以提供优化性的 continuation；关闭该优化后任务仍必须正确完成。

### 9.2 失败分类

```text
FailureDisposition
├── retry_transient
├── resume_checkpoint
├── split_part
├── pause_recoverable
├── fail_permanent
└── cancel
```

分类依据是归一化失败类别、Part 状态、effect 状态、检查点和能力摘要，不是模型名称。

### 9.3 重试约束

- 临时网络/限流错误可以对当前未提交 Invocation 做有界重试。
- 已确认 Provider 拒绝或协议不兼容不得原样重试。
- 不确定工具 effect 不得自动重复执行。
- 自动尝试耗尽进入 `paused`，不是伪装成业务成功。
- `max_model_rounds` 和 `max_progress_rounds` 是单个 Run 的循环保险丝，不是 Long Task 的 Run 数或内容预算；现有数值先保持，真实 E2E 后单独评估。

## 10. Review 的完整性与失败语义

### 10.1 审阅输入

Review Manifest 必须引用被审阅 Draft Revision 及其所需 Parts。Application/Context Port 能按引用读取正文；不能要求模型通过“检查候选稿”猜测正文是否存在。

### 10.2 分集执行

每一必需剧集都是 Manifest Unit，其状态为 `completed`、`waiting_retry`、`needs_split`、`paused`、`failed` 或 `canceled`。

### 10.3 失败传播

- 临时网络错误：有界重试当前集。
- `length`：恢复或细分当前集，不能直接跳到下一集并遗忘它。
- 系统性协议、模型能力或配置错误：立即停止领取后续剧集，避免八次重复失败。
- 已证明只影响本集的数据错误：独立剧集可继续，但失败集进入明确恢复队列。
- Operation 只有在所有必需剧集完成后才能成功。

### 10.4 正式审阅内容

内部异常、工具错误、正文装配失败和 Provider 错误：

- 记录在 Run/Part failure 与诊断事件中；
- UI 最多显示“第 N 集审阅失败”及可操作原因；
- 不得转换为审阅 Issue，也不得参与 Issue 计数、批量处理或 Verdict；
- Review 不完整时不得发布 Candidate Revision。

## 11. Long Task、Operation 与最终完成

### 11.1 聚合层级

```text
Turn
└── Operation
    └── LongTask / ArtifactManifest
        └── Run
            └── ModelInvocation
```

- `max_tokens` 约束 ModelInvocation。
- Run 是一个 Part 的一次执行尝试或一个有界工具阶段。
- Long Task 聚合所有必需 Part。
- Operation 是剧本等产品任务的唯一业务控制权威。

### 11.2 聚合规则

- 全部必需 Part 完成并通过验证：组装 Candidate Revision，Operation `succeeded`。
- 存在可恢复未完成 Part 且没有活动执行：Operation `paused`。
- 存在永久失败且无法满足 Artifact 合同：Operation `failed`。
- 收到取消：Operation `canceled`。
- 部分完成、部分失败绝不能映射为 `succeeded`。

### 11.3 最终输出

- Model reasoning、公开 commentary、工具活动和 Part 进度属于执行过程。
- 子 Run 的 final response 只是 Run 事实，不是产品 Assistant 最终回答。
- 只有 Candidate Revision 已原子写入且 Operation 已成功后，Application 才写入一次 `ASSISTANT_FINAL`。
- 失败、暂停和取消不显示“任务已完成”或产物面板。
- 实时流与历史重放读取同一个 finalization receipt，不能产生两个最终回答。
- 不显示请求开始时的固定编排文案，也不显示固定的“剧本任务已完成”模板；最终文案来自真实 Operation 结果的简洁投影。

## 12. 取消协议

取消命令必须幂等并以持久化状态为事实源：

1. 在短事务中校验目标并原子写入 `cancel_requested` 与命令 receipt。
2. 提交事务后通知活动 worker/Provider cancellation token；不得在数据库事务中等待网络。
3. 调度器停止领取该 Operation 的新 Part，并把尚未领取的 Part 结算为 `canceled`。
4. 活动 Invocation 收到信号后停止流读取；其未验证输出不得提交为 Artifact。
5. Run、Part、Long Task、Operation 和 Turn 按所有权依次结算。
6. 重复取消返回同一 receipt；已进入终态的任务不反向修改。
7. SSE 断开、页面关闭或刷新均不等于取消。

必须消除嵌套事务和“在事务内等待 worker/网络”的路径，避免 `cannot start a transaction within a transaction`。

## 13. 持久化要求

至少持久化 Task Requirements、reasoning mode、Invocation 的模型与能力摘要、Manifest/Part 状态、Artifact ref/digest/validation receipt、termination/failure/disposition/attempt、usage、cancel receipt 和 Operation finalization receipt。

约束：

- completed Part 不得回退或被换模重做。
- 同一 Part 完成 effect 的幂等键唯一。
- 同一 Operation 最多一个 Candidate Revision。
- 同一 Turn 最多一个 finalization receipt 和一个正式 Assistant 最终回答。
- 恢复与重放不复制产物正文，只引用权威 Artifact/Revision。

## 14. 实施边界与迁移顺序

### Phase 1：行为固化

- 为已持久化事故建立脱敏 fixture。
- 固化 10.6K 截断、原样重试、分集跳过、内部错误污染审阅、重复最终输出和取消事务错误。
- 在改生产代码前让新测试证明现有行为错误。

### Phase 2：能力与 Invocation 上限

- 完成版本化 Profile、Task Requirements 和能力预检。
- 让用户覆盖值或 Profile 最大值原样进入 Provider Adapter。
- 删除任务估算、reserve、百分比和 hidden hard cap 对 Provider max 的影响。

### Phase 3：Manifest 与 Part 检查点

- 引入通用 Manifest/Part 状态与 Artifact receipt。
- 先改造剧本正文和 Review 两条高风险配方。
- 宿主确定性组装 Candidate Revision。

### Phase 4：终止与恢复

- 重写 `length`、网络、协议、工具 effect 的处置。
- 删除相同请求原样重试和 reasoning 静默 fallback。
- 增加换模恢复预检与 completed Part 复用。

### Phase 5：Review、聚合与最终输出

- 修复分集失败传播和系统性故障熔断。
- 禁止内部失败生成 Review Issue。
- 以 Manifest 完整性驱动 Operation 成功和唯一最终输出。

### Phase 6：取消与全链路验证

- 重写短事务取消与异步传播。
- 完成故障注入、刷新重放、主动取消和真实 Provider E2E。
- 删除被替代的预算、重试和旧完成投影路径，不保留双轨 fallback。

## 15. 测试矩阵

### 15.1 单元与契约测试

- DeepSeek Profile 的 393216 原样传给 Adapter。
- 用户显式 256K 原样传给 Adapter。
- 不存在按上下文百分比或任务估算缩小 max 的路径。
- 所有已注册 Profile 均通过同一能力 schema 校验。
- Core 源码没有模型名、Provider 名或剧本业务分支。
- 不同 Adapter 将 finish、usage、reasoning 和取消归一成相同协议。
- `length` 不产生相同请求重试。
- 大于单次上限的 Artifact 通过多个 Part 完成。
- 最小 Part 仍截断时明确失败，不无限循环。
- completed Part 在恢复和显式换模后不重做。

### 15.2 Review 与任务状态测试

- 第 1 集系统性失败后不会继续制造第 2–8 集同类失败。
- 已证明局部失败进入恢复队列，已完成独立集保持完成。
- 内部错误不会进入 Review Issue、Issue 数量和 Verdict。
- 存在任一必需未完成集时不能发布 Review Candidate。
- 部分完成不能产生成功消息或产物面板。
- 全部完成后只产生一次最终回答；实时与重放一致。

### 15.3 取消与恢复测试

- 取消停止待领取 Part。
- 活动 Invocation 收到取消并且半截输出不提交。
- 重复取消幂等。
- 取消与完成终态竞争只有一个结果。
- 断开 SSE 不取消任务。
- 进程重启从 Manifest 检查点恢复，不重复已提交 effect。

### 15.4 能力组合测试

不按模型名组织 Core 测试，至少覆盖 selectable reasoning、无 reasoning、always-enabled reasoning、有/无工具调用、reasoning usage included/separate、工具参数完整/截断，以及不同 finish reason 和取消语义。

### 15.5 真实 Provider E2E

发布前必须：

- 使用 DeepSeek 完成思考开启与关闭各一个最小任务；
- 至少使用两个非 DeepSeek Provider/模型完成同一正式任务协议；
- 至少一次显式换模后从未完成 Part 继续；
- 主动制造一次 `length` 或传输中断并证明从检查点恢复；
- 验证工具过程、正文隐藏、Candidate/Revision、取消、刷新与历史重放；
- 测试启动的服务全部关闭并确认端口无监听。

若缺少某 Provider 凭据，不能把 Fake Gateway 测试声称为真实跨模型验证；必须明确列为发布阻塞项。

## 16. 发布门槛

必须同时满足：

1. `npm run check:agent-refactor`；
2. PurrA 与后端完整测试；
3. 前端单测与 TypeScript typecheck；
4. `git diff --check`；
5. 历史事故重放；
6. 预算、length、Review、取消和最终输出故障注入；
7. 全部注册 Profile/Adapter 契约测试；
8. DeepSeek 加至少两个非 DeepSeek Provider 的真实 E2E；
9. Core 中模型名和 Provider 名判断为零；
10. 不存在原样 `length` 重试、内部错误 Review Issue、部分成功 Operation 或重复最终回答；
11. 没有遗留测试服务进程。

## 17. 完成定义

本轮完成不表示模型或网络永远不失败，而表示：

- 单次输出上限不会再被误解为完整任务上限；
- 超过单次输出上限的有限产物，在能够拆成模型可完成的最小 Part 时，通过有边界、可验证、可恢复的 Part 完成；
- 一次调用截断不会触发相同错误循环；
- 已完成内容不会因后续失败或换模丢失；
- Review 只包含真实审阅意见，内部错误保留在执行诊断；
- 所有必需 Part 完成前，系统不会谎报任务成功；
- 取消能够可靠终止活动与待执行工作；
- DeepSeek、智谱、Kimi、MiMo 和后续模型共享同一 PurrA 状态机，差异只存在于能力档案和 Adapter；
- 用户最终只看到一次与权威 Candidate/Revision 一致的正式结果。
