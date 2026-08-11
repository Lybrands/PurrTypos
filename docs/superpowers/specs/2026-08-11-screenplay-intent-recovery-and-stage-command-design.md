# 剧本 Agent 意图恢复与阶段命令设计

日期：2026-08-11

状态：已确认

范围：剧本阶段按钮、Conversation Turn、意图 Planner、PurrA 托管模型调用、结构化 JSON 修复与 Turn/Operation 提交边界

## 背景

真实事故 `spaturn_d0efbd6a6f824fd084559d84440617bd` 的用户输入是“开始审阅”。第一次模型调用已经生成正确的 `review` 意图，但完整 JSON 只出现在 `reasoningDelta`，正式 `content` 为空。剧本结构化调用把“没有候选”误判为“候选 JSON 损坏”，随后用空字符串执行 repair。第二次模型把 repair 指令理解为业务问题，返回“未收到需要修复的候选 JSON”的合法 `answer` 意图。该意图通过结构校验后被提交为完成态，最终没有创建 Operation 或 Task。

这不是一次普通的意图识别错误，而是三项契约同时失效：

1. 阶段按钮已知的业务动作在 Conversation 请求中退化为普通文本；
2. Application 层自建的结构化恢复循环把 empty/reasoning-only 与 invalid candidate 合并；
3. Turn 提交边界只验证 Intent 结构，没有验证结果与原始阶段命令相容。

此前的长篇阶段模板实际上承担了隐式命令协议。删除模板是正确的，但同时暴露出正式任务缺少结构化命令边界。本设计取代 `2026-08-11-remove-canned-stage-prompts-design.md` 中“不引入新的结构化按钮命令协议”这一阶段性非目标；其余关于简短用户动作、可选模型概述和执行面板的设计保持有效。

## 目标

1. “开始审阅”等确定性正式阶段按钮保留不可被模型改写的业务边界，同时继续经过统一 Planner 和任务系统。
2. 自由文本仍由模型完成意图判断，不依赖固定短语或前端枚举自然语言。
3. reasoning-only、empty、invalid JSON、schema invalid、truncated 和 provider failure 具有不同终态与恢复路径。
4. 只有存在非空候选时才允许 JSON repair；空输出不得进入 repair。
5. 模型私有 reasoning 永远不直接成为业务输出或用户可见正式答复。
6. 存在阶段命令的 Turn 不得以不相容的 `answer` 完成。
7. 结构化模型调用的空响应恢复归 PurrA，剧本 Application 只拥有 JSON/领域验证和业务提交。

## 非目标

- 不恢复长篇自动模板，不把业务规则重新塞进用户消息。
- 不按“开始审阅”等固定中文短语在后端猜测命令类型。
- 不让阶段按钮绕过 Planner；Planner 仍负责生成完整 instruction、约束整理和公开概述。
- 不把“处理审阅意见”等普通对话快捷入口强制转换成正式 Operation。
- 不解析或公开 chain-of-thought。
- 不修改已完成历史 Turn；事故记录继续作为审计事实保留。
- 不重写完整 Agent Runtime，也不为剧本建立第二套通用 Runtime。
- 不改变审阅正文、五维检查、Artifact、Revision 或最终裁决规则。

## 方案选择

### 方案 A：在 `run_json()` 增加空字符串分支

对空 content 重试原请求，其他逻辑不变。改动最小，但 Application 仍拥有重复恢复状态机，阶段命令仍会丢失，语义漂移仍可能被提交。拒绝。

### 方案 B：结构化阶段命令 + Core 空响应恢复 + 业务相容性校验

阶段按钮携带结构化命令；PurrA 托管模型调用统一处理 empty/reasoning-only；剧本结构化调用只修复非空候选；提交前验证 Intent 与命令相容。该方案修复全部失效边界，同时复用现有 Planner、Resolver、Operation 和 Runtime 契约。采用。

### 方案 C：结构化调用全部改走完整 Agent Runtime

可以直接复用完整 Runtime 的恢复状态机，但会把无工具、单结果的内部结构化调用包装成完整 Agent 轮次，增加事件、工具和终态适配复杂度。当前没有必要。拒绝。

## 核心不变量

1. 正式阶段按钮的文字只用于对话展示；`stageCommand` 才是该按钮的业务边界。
2. `stageCommand.action` 不允许为 `answer`。
3. Planner 可以补全意图，但不得改变 `stageCommand` 指定的 action、targetRole 或精确范围。
4. 自由文本 Turn 没有 `stageCommand`，仍可规划为 answer/create/revise/review。
5. reasoning 与 content 是不同通道；reasoning 的存在只能参与恢复诊断和协议允许的 replay。
6. candidate repair 的前置条件是“存在非空 content，且该 content 解析或验证失败”。
7. 模型截断、Provider 失败和无正式输出都不是候选 JSON 损坏。
8. 存在 `stageCommand` 的 Turn 只能创建与命令相容的 Operation，不能走 `complete_answer()`。
9. PurrA 拥有模型调用终态分类、reasoning replay 和空响应恢复预算；剧本 Application 拥有 Intent/业务校验。

## Conversation 命令契约

`SubmitScreenplayAgentTurnRequest` 增加可选字段：

```ts
type ScreenplayStageCommand = {
  kind: 'stage_action'
  action: 'create' | 'revise' | 'review'
  targetRole:
    | 'sourceAnalysis'
    | 'creativeBrief'
    | 'structure'
    | 'sceneList'
    | 'screenplayDraft'
    | 'review'
  scope: {
    kind: 'current_stage' | 'next_episodes' | 'episodes' | 'all_remaining'
    count?: number
    episodeNumbers?: number[]
  }
}
```

规则：

- 创建、修订或审阅交付物的正式阶段按钮和批量正文按钮提交 `stageCommand`；自由输入省略。
- “处理审阅意见”等只触发普通对话的快捷入口省略 `stageCommand`，允许 Planner 合法返回 answer，并且不得创建 Operation。
- `content` 继续保存并显示用户实际点击的简短动作，例如“开始审阅”。
- 前端通过项目 Workspace 和按钮本身生成枚举值，不从按钮 label 反向解析。
- 后端在持久化前校验 action、targetRole、scope 的组合；非法组合拒绝入队。
- Turn 增加 nullable `stage_command_json`。恢复、重启、重放和幂等提交都从持久化命令读取，不依赖页面内存。
- 旧 Turn 的 `stage_command_json` 为 null，按自由文本历史处理。
- Snapshot 返回该字段以支持确定性重放和诊断，但普通对话展示仍只使用 `userContent`。

## Planner 与 Resolver 边界

Planner 输入增加可选的 `requiredStageCommand`。它不是提示建议，而是宿主约束：

- `action` 必须与命令一致；
- `requestedDeliverable` 必须等于 `targetRole`；
- 精确集数、批量数量或 all_remaining 范围不得被扩大、缩小或改型；
- `instruction`、`constraints`、`preserve` 和可选 `executionSummary` 仍由模型结合 Workspace 与对话上下文生成。

Planner 返回后，剧本域验证器执行相容性校验。初次输出和 repair 输出使用同一验证器，因此 repair 不能把 `review` 改成 `answer`。不相容结果不是合法 Intent，不能写入 Turn。

Resolver 继续使用持久化项目事实确定先决条件、Head、Draft Revision 和具体 Manifest。`stageCommand` 不携带正文、版本 ID 或数据库事实，也不能绕过 Resolver。

## PurrA 托管文本调用

PurrA 的托管模型边界增加一个通用的流式文本收口能力，供无工具的宿主子调用使用。它消费 Provider stream，并通过回调保留现有诊断、usage 和公开字段投影，同时返回结构化终态：

```text
ManagedTextResult
├── content
├── reasoning
├── finish_reason
├── usage
└── attempts
```

PurrA 在该边界内负责：

- finish reason 与截断分类；
- content/reasoning 累积；
- reasoning-only 与完全 empty 的识别；
- 使用共享 `RecoveryPolicy`/`RecoveryLedger` 消耗 `EMPTY_MODEL_RESPONSE` 预算；
- 按模型能力执行 reasoning replay；
- 重试仍为空时抛出稳定错误 `empty_model_response`。

空响应重试复用原始业务消息，并追加上一轮 Assistant reasoning 和明确要求正式 content 的 Developer 指令。它不切换用户选择的 thinking 模式，不把 reasoning 复制到 content，也不调用 JSON repair。

完整 Agent Runtime 与托管文本调用共享空响应恢复指导、原因枚举和预算策略，避免两份不同语义的实现。托管文本调用仍然保持 no-tools，不引入完整 Agent 工具循环。

## 结构化 JSON 状态机

`ScreenplayStructuredCallService` 只保留以下状态：

```text
CALL_ORIGINAL
  ├── non-empty valid candidate ───────────────> SUCCESS
  ├── non-empty invalid candidate ─────────────> REPAIR_CANDIDATE
  ├── empty/reasoning-only ────────────────────> Core 内恢复或失败
  ├── truncated ───────────────────────────────> FAIL_TRUNCATED
  └── provider/protocol failure ───────────────> FAIL_PROVIDER

REPAIR_CANDIDATE
  ├── non-empty valid compatible candidate ────> SUCCESS
  └── 其他结果 ─────────────────────────────────> FAIL_STRUCTURED_OUTPUT
```

`REPAIR_CANDIDATE` 的用户消息必须是原始非空候选。repair 提示不能替代原始业务请求，也不能在候选为空时运行。repair 最多一次；PurrA 内部的 empty-response retry 与 JSON repair 是不同恢复原因、不同预算。

结构化调用不得把 reasoning 作为 candidate。reasoning 只保存在受控诊断 chunk 中。

## 能力预检

当 `json_object_output=True` 时，模型请求必须声明 `structured_output_level='json_object'`，不能继续以 `none` 通过预检后再附加 `response_format`。

用户选择的 thinking 模式继续保持不变。系统必须正确处理该模式下的 reasoning-only，而不是通过静默关闭 thinking 规避问题。若模型 Profile 不支持请求的 JSON 输出等级或 reasoning 模式中的任一能力，应在调用前以能力错误失败。

## 提交与失败边界

`ScreenplayAgentService.execute_turn()` 在提交前执行最终防线：

- 无 `stageCommand` 且 Intent 为 answer：允许 `complete_answer()`；
- 有 `stageCommand` 且 Intent 为 answer：以 `screenplay_intent_command_mismatch` 失败；
- 有 `stageCommand` 且 action/target/scope 不相容：同样失败；
- 有合法阶段命令与 Intent：Resolver、Manifest、Operation、Long Task 按现有单路径继续。

任何 mismatch 都不得创建 Operation、Task、Artifact 或 Revision，也不得写入模型生成的错误答复。Turn 保留失败事实和 Planner Run，方便诊断与重试。

采用以下稳定错误码：

- `empty_model_response`：模型在受限重试后仍无正式输出；
- `structured_output_invalid`：存在候选，但初次和一次 repair 均未形成合法结构；
- `screenplay_intent_command_mismatch`：Planner 结果与持久化阶段命令不相容；
- 现有 `model_output_truncated` 和 Provider 错误码保持不变。

## 数据流

```text
用户点击“开始审阅”
  -> content="开始审阅"
  -> stageCommand={action:review,targetRole:review,scope:current_stage}
  -> 原子持久化 Turn + stageCommand
  -> Planner 读取 Workspace、对话历史和 requiredStageCommand
  -> PurrA 托管文本调用处理 stream/empty/reasoning-only
  -> Screenplay Structured Call 解析非空 content
  -> Intent 结构校验 + stageCommand 相容性校验
  -> Resolver 绑定当前 Draft Revision
  -> 创建 review Operation 和 Task
  -> 执行、校验并发布候选审阅 Revision
```

自由文本的数据流相同，但没有 `requiredStageCommand`，因此 Planner 可以合法选择 answer 或正式任务。

## 测试设计

### PurrA Core

- reasoning-only 后重放原始请求并得到正式 content；
- 连续 reasoning-only 耗尽预算后返回 `empty_model_response`；
- 完全 empty 与 reasoning-only 都不被分类为候选损坏；
- 截断不重试、不 replay、不进入 repair；
- reasoning replay 遵守 Profile 的 required/forbidden/ignored 能力；
- thinking 模式在所有物理尝试中保持不变。

### 结构化调用

- 非空 malformed JSON 才进入一次 repair；
- 空 content 不调用 repair；
- 第一次 reasoning-only、Core 重试返回合法 JSON 时成功；
- repair 返回结构合法但业务不相容的 Intent 时失败；
- `executionSummary` 仍为可选，不影响合法 Intent；
- Planner、审阅分片和其他共享 `run_json()` 调用都使用相同状态机。

### Conversation 与领域

- 阶段按钮命令与简短 content 一起持久化；
- 重启和 resume 后仍使用原 stageCommand；
- `review` 命令不能完成为 answer/create/revise；
- 合法 review 命令必须创建 targetRole=review 的 Operation 和 Task；
- 普通咨询允许 answer，且 Operation、Task、Revision 数量保持为零；
- 幂等重复提交返回同一 Turn，不生成第二个命令或 Operation。

### 前端

- 自动按钮根据结构化状态生成 stageCommand，不解析 label；
- 对话仍只展示简短用户动作，不显示 stageCommand JSON 或长模板；
- 自由输入不携带 stageCommand；
- 批量创作保留精确 count/scope；
- 实时与历史重放显示一致。

### 事故回放

固定回归脚本：第一次返回正确 review JSON 但全部位于 reasoning，第二次托管空响应重试返回正式 JSON。期望 Turn 进入 review Operation；任何“没有待修复 JSON”的 answer 都必须被拒绝。

## 迁移与发布顺序

1. 增加 nullable `stage_command_json` 和请求/持久化契约，旧客户端仍可省略。
2. 在 PurrA 托管模型边界实现共享空响应收口，并让 Structured Call 使用它。
3. 收紧 JSON repair 前置条件，增加 Intent 相容性验证。
4. 在 Service 提交边界增加阶段命令防线。
5. 前端阶段按钮开始发送 stageCommand；自由文本路径保持不变。
6. 更新 V2 API 和删除设计文档中的过时非目标。
7. 运行事故回放、架构门禁、前后端完整测试和可用真实 Provider E2E。

迁移期间不保留字符串识别 fallback。后端可以兼容没有 `stageCommand` 的旧客户端和自由文本，但新阶段按钮必须在同一版本内切换为结构化命令。

## 验收标准

1. 点击“开始审阅”后，即使第一次模型只产生 reasoning，系统也不会进入空候选 repair。
2. 成功恢复时最终 Intent 仍为 review，并创建 review Operation 与 Task。
3. 重试耗尽时 Turn 明确失败为 `empty_model_response`，不会伪装成普通 answer 完成。
4. repair 永远接收非空候选，且不能改变阶段命令的 action、targetRole 或范围。
5. 普通自由问答行为不变，不创建任何正式业务对象。
6. 用户对话中不重新出现宿主长模板或结构化命令 JSON。
7. PurrA 与剧本 Application 之间只保留一套空响应恢复语义。
8. 刷新、断线、重启、resume 和幂等重试都保留相同 stageCommand 与业务结果。
9. 架构门禁、TypeScript 类型检查、前端测试、后端完整测试和事故回放全部通过。
