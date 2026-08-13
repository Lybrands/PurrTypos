# 小说与剧本 Agent 动态规划设计

## 状态

- 日期：2026-08-13
- 范围：小说 Agent、剧本 Agent、产品装配和共享对话投影
- 结论：采用“LLM 公开动态计划 + 宿主能力边界 + 剧本私有耐久 Recipe”
- 本文只定义设计，不授权实施

## 1. 目标

本次改造解决以下问题：

1. 小说 Agent 和剧本 Agent 都应使用 LLM 动态规划，不再向用户展示宿主硬编码的机械步骤。
2. 用户可见的任务进度只表达 LLM 对当前目标的语义拆解；Recipe、Part、校验和发布步骤属于内部执行结构。
3. 剧本 Agent 保留阶段交付物、耐久分片、检查点、恢复和原子发布能力，但不再自行拥有第二套 Run 生命周期。
4. Run、Operation、Recipe、Artifact、Revision 和 Conversation Turn 各自只有一个清晰的状态职责。
5. 小说与剧本业务复用 PurrA 已有动态规划、工具循环、授权、取消、恢复和事件协议，不把业务语义写入 Core。

## 2. 非目标

- 不公开模型私有 reasoning 或 chain-of-thought。
- 不让 LLM 直接控制剧本 Recipe 的依赖、重试、并行度、幂等键或发布顺序。
- 不删除 PurrA 的 Child Run 和 Delegation 能力。
- 不新增数据库状态机。
- 不在共享前端中加入小说或剧本业务分支。
- 第一阶段不删除旧数据库字段；先停止把重复字段作为权威，再单独评估清理。
- 不把模拟 Provider 测试表述为真实模型端到端验证。

## 3. 总体架构

### 3.1 共同原则

- Prompt 定义目标、行为原则、业务上下文、硬约束、成功条件和公开说明要求。
- LLM 负责生成用户可见 `TaskPlan`、选择工具、观察结果并调整尚未完成的计划。
- 宿主负责范围、权限、工具参数、取消、恢复、幂等、Artifact 校验和原子发布。
- 用户可见任务计划只有一个来源：Root Run 上由 LLM 产生的语义计划。
- 宿主 Recipe、Part、校验与发布步骤只进入内部进度、执行过程或诊断，不替代用户计划。

### 3.2 PurrA Core

PurrA 保持业务无关，继续负责：

- 动态规划和重规划；
- `TaskPlan`、步骤依赖和状态校验；
- 模型与工具循环；
- Run、Child Run 和 Delegation 生命周期；
- 授权、审批、取消和超时；
- 上下文压缩、事件、持久化和恢复；
- usage、终态和输出事务。

本设计预期不修改 `packages/purra`。若实施中发现缺少必要的通用契约，必须先证明该能力同时适用于不同业务 Profile，再单独提出 Core 变更，不能从剧本或小说语义反向污染 Core。

## 4. 小说 Agent 设计

### 4.1 规划模式

小说 Agent 使用连续动态规划：

```text
用户请求
  -> Root Run
  -> LLM 生成公开 TaskPlan
  -> 按需读取章节、设定、人物、伏笔、故事记忆或写作方法
  -> 执行工具
  -> 根据结果调整尚未完成的步骤
  -> 生成候选修改或最终回答
  -> Root Run 原子完成
```

LLM 可以在执行期间增加、删除、合并或重排未完成步骤。已经完成的步骤保留为执行历史，不允许被重写成未发生的动作。

### 4.2 业务职责

- `WritingPlanningPolicy` 只判断是否需要规划，并收紧本轮允许使用的工具、角色和能力。
- Writing Prompt 明确创作目标、正典硬约束、公开 commentary、候选稿边界和停止条件。
- `WritingContextProvider` 在规划阶段只提供轻量事实，正文和业务资料通过工具按需读取。
- Writing Tool Policy 限制书籍、章节、写作方法和数据写入范围。
- 正文修改先形成候选内容；未经用户确认，不覆盖正式章节。

### 4.3 上下文约束

- 人物身份、世界规则、已发生事件和角色知识属于硬约束。
- 风格、节奏、视角、对白和描写习惯属于可调整软约束。
- 不把整本小说一次性放入 Prompt；根据当前计划、TaskSpec 和预算按需检索。
- 书籍绑定、章节身份和写作方法版本由宿主解析并注入，LLM 不能自行升级、解绑或改变优先级。

## 5. 剧本 Agent 设计

### 5.1 双层编排

剧本 Agent 同样支持动态规划，但保留业务耐久边界：

```text
用户请求
  -> Root Run 生成公开 TaskPlan 与业务 TaskSpec
  -> Screenplay Resolver 校验阶段、范围、交付物和来源
  -> Screenplay Manifest Compiler 编译私有 Recipe
  -> 执行宿主 Part 和必要的 AI Child Run
  -> 检查点之间允许调整未完成的语义计划
  -> Candidate Artifact
  -> Screenplay Operation 原子发布
  -> Root Run 提交最终 Assistant 回复
```

公开计划表达“要解决什么问题”；私有 Recipe 表达“如何可靠、可恢复地完成业务产物”。两者通过稳定的计划步骤映射关联，但不能互相替代。

### 5.2 语义规划

根 Turn 的 LLM 规划结果同时包含：

- 用户可见 `TaskPlan`；
- 目标交付物；
- `answer | create | revise | review` 动作；
- 当前阶段、下一批集数、明确集数或全部剩余范围；
- 用户明确限制；
- 修改时必须保留的内容。

普通问答直接由 Root Run 回答，不创建 Screenplay Operation、Recipe、Candidate 或 Revision。

### 5.3 业务解析与准入

Screenplay Resolver 使用持久化项目事实校验：

- 当前工作流阶段；
- 前置交付物；
- 目标角色与 Artifact 类型；
- 集数和 Scene 范围；
- Source、Revision 和只读原作边界；
- 当前 Candidate、Head 和基础 Revision；
- 用户请求与正式按钮命令是否一致。

LLM 可以提出计划，但不能绕过这些准入条件。非法目标在 Recipe 创建前被拒绝或转为明确澄清。

### 5.4 私有 Manifest 与 Recipe

- Manifest Compiler 从已验证 TaskSpec 生成稳定语义 Part 和依赖。
- Recipe、Part、校验、组装和发布步骤不作为公开任务计划发送。
- 多个内部 Part 可以映射到同一个公开语义步骤。
- 已完成 Part、已产生 Artifact 和已接受 Revision 在重规划时保持不变。
- 只改变创作策略或工具选择时，继续当前 Operation。
- 改变阶段、交付物、集数或发布范围时，重新经过 Resolver，并重新编译尚未完成的 Recipe；不得修改已完成历史。

## 6. Run、Operation 与内容权威

### 6.1 最小执行拓扑

```text
Conversation Turn
└── Root Run
    ├── 普通问答：直接完成
    └── Screenplay Operation
        └── 私有 Recipe
            ├── 宿主确定性 Part：不创建 Run
            ├── AI Part：PurrA 按需创建 Child Run
            ├── 可选 Delegation：子 Agent Child Run
            └── 宿主发布 Part：不创建 Run
```

### 6.2 唯一状态所有者

| 事实 | 唯一权威 |
|---|---|
| 用户可见计划与公开执行过程 | Root Run |
| 模型或工具执行 | Root Run / Child Run |
| 剧本正式业务任务 | Screenplay Operation |
| 耐久分片、依赖与检查点 | Recipe / WorkItem |
| 候选内容 | Artifact |
| 已接受正式内容 | Revision / Head |
| 消息与 Turn 关系 | Conversation Turn |
| 页面展示 | SSE 和持久化快照的投影 |

Conversation Turn 可以保留关联信息，但不再维护一套独立的 `planning/running/failed/canceled` 执行权威。Root Run 在规划完成后不能提前结束；正式任务中，它持续到 Operation 成功、失败、暂停或取消。

### 6.3 剧本业务中的 Run 简化

移除剧本业务层对 Child Run 生命周期的控制，包括：

- 自行创建和绑定 Child Run；
- 自行同步父子 Run 状态；
- 自行实现取消传播和重启恢复；
- 把独立 Planner Run 当作耐久任务父 Run；
- 为每个机械 Recipe Part 创建 Run。

保留 PurrA 的 Root Run、Child Run 和父子谱系。剧本业务只声明需要执行的 AI Part，PurrA 决定并管理实际 Child Run。

## 7. Child Run 与子 Agent

- Child Run 是父 Run 下的一次独立执行记录，不等于子 Agent。
- 子 Agent 是带 `AgentDelegation`、明确角色、目标、工具权限和结果聚合的委派执行者。
- 所有子 Agent 都通过 Child Run 执行，但并非所有 Child Run 都是子 Agent。
- 剧本 Agent 默认不主动拆成多 Agent。
- 只有 LLM 明确需要独立研究、审校或并行判断时才创建子 Agent。
- 必需子 Agent 失败会阻塞对应步骤；可选子 Agent 失败只记录过程，主 Agent 可以继续。
- 确定性证据引用、Schema 校验、Artifact 组装、状态汇总和原子发布不创建 Run。

## 8. 取消、恢复与失败语义

### 8.1 取消

- 用户取消 Root Run 时，向下取消 Screenplay Operation、LongTask、活跃 Child Run 和子 Agent。
- SSE 断开只移除订阅者，不能取消任务。
- 取消完成后不得遗留 `running` Run、Part 或 Operation。
- 已经写入的有效 Artifact 和完成检查点保留，但不得发布正式成功结果。

### 8.2 恢复

- 页面刷新从 Root Run 快照恢复公开计划与过程。
- Screenplay Operation 和 Recipe 检查点恢复业务进度。
- 已完成 Part 不重新执行。
- 模型切换只影响未完成 Child Run；切换前暂停并进行能力兼容性校验。
- 应用重启后恢复未终止 Root Run、Operation 和耐久执行单元，不通过前端缓存猜测状态。

### 8.3 失败

- LLM 计划结构错误允许一次结构修复；仍失败则 Root Run 失败，不创建 Operation。
- 业务范围非法由 Resolver 阻止，不编译 Recipe。
- Child Run 失败按错误分类重试、暂停或失败，保留已完成 Part。
- Artifact 校验失败时保留 Artifact 和诊断证据，不发布 Revision。
- 发布操作必须原子、幂等；重复请求不得产生重复 Revision。
- 必需子 Agent 失败阻塞计划；可选子 Agent 失败不自动失败整个任务。

## 9. 产品装配与前端投影

### 9.1 产品装配

- Writing 和 Screenplay Profile 都通过产品侧 `composition_factory.py` 注入。
- 清理通用 `agent_composition.py` 中默认写死的 Writing 业务装配。
- 该调整属于产品装配边界，不是 PurrA Core 业务化。

### 9.2 共享前端

- 共享对话组件只消费标准公开 `TaskPlan`，不判断小说或剧本。
- 执行过程保留公开 commentary、真实工具、上下文压缩和内部委派。
- Recipe、宿主校验和模型生命周期不进入用户任务计划。
- 实时 SSE 与历史 replay 必须得到相同的计划、过程、终态和滚动结构。
- 原则上复用现有 UI；仅在协议投影确实需要时调整通用映射，不新增第二套前端状态。

## 10. 预计代码范围

### 10.1 小说业务

- `backend/domains/writing/planning.py`
- `backend/domains/writing/prompts.py`
- `backend/domains/writing/context.py`
- `backend/domains/writing/tools/**`
- 对应 Writing 单元与集成测试

### 10.2 剧本业务

- `backend/application/screenplay_agent_planner.py`
- `backend/application/screenplay_agent_service.py`
- `backend/application/screenplay_manifest_compiler.py`
- `backend/application/screenplay_agent_task_executor.py`
- `backend/domains/screenplay_agent/adapter.py`
- `backend/domains/screenplay_agent/contracts.py`
- 对应 Screenplay、Operation、取消和恢复测试

### 10.3 产品装配与共享投影

- `backend/application/composition_factory.py`
- `backend/application/agent_composition.py`
- 必要时修改 `src/agent-runtime/**` 的通用计划投影与回放测试
- 不预期修改 `packages/purra/**`

## 11. 实施拆分

该设计应拆成三个可独立评审和验收的实施计划：

1. Root Run、公开计划可见性和产品装配边界收口。
2. 小说 Agent 连续动态规划与 Prompt/上下文约束改造。
3. 剧本 Agent 动态语义计划、私有 Recipe 映射、Run 所有权和检查点重规划改造。

计划 1 是后两项的共同前置；计划 2 和计划 3 在前置完成后可以分别实施和验收。

## 12. 验收标准

### 12.1 小说 Agent

- 工具结果能够触发对尚未完成步骤的动态调整。
- 任务进度来自 LLM 计划，不包含宿主固定文案。
- 章节、正典、设定和写作方法权限由宿主约束。
- 未经确认的候选修改不会覆盖正式章节。

### 12.2 剧本 Agent

- 普通问答只有 Root Run，不创建 Operation。
- 正式任务只有一个 Root Run 和一个 Screenplay Operation。
- 公开计划来自 LLM，不来自 `_durable_plan`、Manifest 标题或 Recipe 单元。
- Recipe、校验和发布步骤不出现在任务进度。
- 确定性 Part 不创建 Child Run。
- AI Part 的 Child Run 由 PurrA 管理，剧本业务不拥有其生命周期。
- 默认不创建子 Agent；明确委派时才创建 Delegation Child Run。
- 检查点重规划不重做完成 Part，不覆盖已有 Artifact 或 Revision。

### 12.3 生命周期与投影

- 手动取消关闭 Root Run、Operation、LongTask、活跃 Child Run 和子 Agent。
- SSE 断线后任务继续，重新订阅可恢复相同页面状态。
- 应用重启后从持久化检查点继续。
- 重复发布不产生重复 Revision。
- 实时和历史回放的计划与执行过程一致。

### 12.4 验证门槛

- `npm run check:agent-refactor`
- 后端相关测试与完整 pytest 门槛
- 前端相关测试、TypeScript typecheck 和回放测试
- `git diff --check`
- 使用小范围真实 Provider 完成一次小说和一次剧本 E2E，覆盖动态计划、工具、取消/恢复和候选产物；缺少凭据时必须标记为发布阻塞项，不能用模拟 Gateway 测试代替。

