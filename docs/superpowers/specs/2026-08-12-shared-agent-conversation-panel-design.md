# 通用 Agent 对话面板整理设计

## 状态

- 日期：2026-08-12
- 状态：已确认，待实施计划
- 范围：前端通用 Agent 对话运行时与业务组件
- 不改变：PurrA 协议、业务领域模型、后端持久化结构和模型 Provider

## 背景

PurrTypos 已经有 `AgentConversation`、`AgentComposer`、`AgentConversationIndex`
等共享组件，但当前共享边界并不成立：

- 书籍 AI 仍由 `Workspace/AiPanel` 独立拥有虚拟消息列表、消息气泡、编辑、复制、
  执行日志和输入区组合；
- 剧本 Agent 使用 `src/components/AgentConversation`，但该组件反向引用
  `Workspace/AiPanel` 内的 Assistant 消息体和错误展示；
- `src/agent-runtime` 对外宣称是共享运行时，实际又重新导出
  `Workspace/AiPanel/hooks` 中的消息类型、chunk handlers、历史转换和工具；
- `AgentTaskProgress`、回合索引和剧本页面也直接引用 `Workspace/AiPanel` 内的
  TaskPlan、Markdown、ModelPicker 等实现；
- 因此一次执行日志、任务胶囊、输出可见性或滚动行为的修复，可能只命中某一种
  对话入口，或者必须在产品页面目录内修复所谓“共享”行为。

这不是目录命名问题，而是所有权和依赖方向错误。对话面板应成为通用的业务组件，
所有需要 AI 对话的模块都消费同一套消息、执行过程和输入交互；书籍、小说、剧本等
业务只负责提供数据和领域扩展。

## 目标

1. 建立一个完整的 `AgentConversationPanel`，统一会话索引、消息区、执行过程、任务进度、
   输入器和对话操作。
2. 将消息类型、Canonical 输出归约、chunk replay、终态处理和会话运行状态归还
   `src/agent-runtime`。
3. 让书籍 AI、剧本 Agent 和后续小说 Agent 通过业务 Controller 接入同一面板。
4. 删除 `AgentConversation -> Workspace/AiPanel` 和
   `agent-runtime -> Workspace/AiPanel` 的反向依赖。
5. 保留当前已经确认的交互：真实模型文案、单一执行折叠面板、任务胶囊、逐操作计时、
   终止、恢复、编辑、复制和上下文用量。

## 非目标

- 不把 React UI 放进 `packages/purra`；PurrA 仍只拥有通用 Agent 协议和执行语义。
- 不创建可动态安装的插件系统、IOC 容器或通用页面框架。
- 不统一书籍、小说和剧本的领域数据、Artifact 或阶段命令。
- 不在本次整理中重写后端 SSE、Run、Operation、LongTask 或持久化实现。
- 不为了目录统一而搬迁 Prompt 模板、记忆中心、书籍上下文等明确的产品功能。

## 设计原则

### 1. 通用面板拥有交互，业务 Controller 拥有数据接入

`AgentConversationPanel` 管理对话的公共交互和展示状态。业务模块提供一个稳定的
Controller，其中包含面板需要的状态和命令。面板不能导入书籍、小说或剧本模块。

### 2. Canonical 输出是唯一执行读模型

消息正文、公开 commentary、Operation、子 Agent、任务计划和终态全部来自
`agent-runtime` 归约结果。业务页面不能再次从工具参数、Artifact 或宿主模板生成
Assistant 文案，也不能自行重建执行日志。

### 3. 插槽只承载领域差异

插槽用于剧本 Revision、小说候选稿、书籍设定卡、项目上下文等真正的领域内容。
复制、编辑、错误、工具审批、任务胶囊、执行日志、模型标签和发送/终止不属于插槽，
由通用面板统一实现。

### 4. 依赖只能由产品指向通用层

允许：

```text
ScreenplayAgentPage ─┐
NovelAgentPage ──────┼─> AgentConversationPanel ─> agent-runtime
Workspace/AiPanel ───┘
```

禁止：

```text
AgentConversationPanel -> Workspace/AiPanel
AgentConversationPanel -> ScreenplayAgentPage
agent-runtime -> Workspace/AiPanel
```

## 目标目录与所有权

```text
src/
  agent-runtime/
    contracts.ts
    canonicalOutput.ts
    chunkHandlers/
    chunkReplay.ts
    chatHistory.ts
    runtimeStore.ts
    taskPlan.ts
    contextBudgetProjection.ts
    index.ts

  components/
    AgentConversation/
      AgentConversationPanel.tsx
      ConversationIndex/
      ConversationViewport/
      Message/
      AssistantOutput/
      ExecutionLog/
      TaskProgress/
      Composer/
      StructuredQuestion/
      ToolApproval/
      controller.ts
      extensions.ts
      index.ts

    Markdown/

  Workspace/AiPanel/
    useBookConversationController.ts
    BookConversationExtensions.tsx
    ...书籍上下文、记忆、收藏、Prompt 模板等产品能力

  ScreenplayAgentPage/
    useScreenplayConversationController.ts
    ScreenplayConversationExtensions.tsx
    ...剧本项目、Revision、审阅和阶段能力
```

目录名称描述所有权而不是使用地点。通用 Markdown 独立放在
`src/components/Markdown`，因为项目文档、回合索引和 Agent 消息都需要它。

## 公共 Controller 契约

Controller 是面板唯一的业务入口。它按职责分组，避免向组件传递大量互不相关的 props。

```ts
export interface AgentConversationController {
  capabilities: AgentConversationCapabilities
  conversation: {
    sessions: AgentConversationSession[]
    activeSessionId: string | number | null
    messages: AgentConversationMessage[]
    initializing: boolean
    running: boolean
    queuedSubmissions: AgentQueuedSubmission[]
    history?: {
      sessions: AgentConversationSession[]
      loading: boolean
      error?: string
    }
  }
  composer: {
    value: string
    setValue(value: string): void
    submitDisabled: boolean
    contextUsage?: AgentContextUsage
    selectedModel?: AgentModelSelection
    taskPlan?: AiTaskPlan
  }
  actions: {
    selectSession(id: string | number): void | Promise<void>
    createSession(): void | Promise<void>
    closeSession(id: string | number): void | Promise<void>
    renameSession(id: string | number, title: string): void | Promise<void>
    loadSessionHistory?(): void | Promise<void>
    deleteSession?(id: string | number): void | Promise<void>
    send(): void | Promise<void>
    abort(): void | Promise<void>
    editMessage(index: number, content: string): void | Promise<void>
  }
}
```

`capabilities` 复用现有 `AgentConversationCapabilities`，统一输入禁用、会话导航和
发送/排队模式。`queuedSubmissions` 保留面板展示待发送正文所需的最小读模型；历史会话
由面板统一展示和交互，但查询范围仍由业务 Controller 决定。具体字段在实施计划中根据
现有稳定类型收敛，不为未来可能出现的产品预留抽象。Controller 不暴露底层 SSE、
数据库记录或业务 Aggregate。

## 领域扩展契约

通用面板只保留以下领域扩展点：

```ts
export interface AgentConversationExtensions {
  renderSessionContext?(): React.ReactNode
  renderComposerLeading?(): React.ReactNode
  renderAssistantAttachment?(
    message: AgentConversationMessage,
    index: number,
  ): React.ReactNode
}
```

- `renderSessionContext`：书籍/项目名称、阶段等会话上下文；
- `renderComposerLeading`：章节、大纲、记忆或业务范围选择；
- `renderAssistantAttachment`：Revision、Artifact、候选稿、设定 diff 等领域结果。

若某个扩展同时被两个以上业务模块需要，应提升为通用组件，而不是继续增加插槽。

## 通用组件职责

### AgentConversationPanel

- 组合会话索引、消息区和输入器；
- 根据 Controller 状态控制加载、只读、发送、终止和会话导航；
- 统一历史会话的搜索、打开和删除交互，业务 Controller 只负责按当前领域范围查询；
- 不请求网络，不理解业务阶段，不直接访问数据库或服务单例。

### ConversationViewport

- 统一历史消息、虚拟列表和普通列表的滚动跟随语义；
- 用户向上滚动后停止自动跟随，返回底部后恢复；
- 维护回合索引和定位，不由业务页面重复实现。

第一阶段可以保留现有两种渲染后端，但必须共享同一消息组件和滚动策略；最终只保留
能同时满足长对话和普通对话的实现。

### Message 与 AssistantOutput

- 统一用户消息、Assistant 消息、编辑、复制、模型标签、错误和终止状态；
- Assistant 正文只渲染 `agent-runtime` 提供的公开输出；
- 结构化问题和工具审批属于通用 Agent 能力；
- 领域卡片通过 `renderAssistantAttachment` 注入。

### ExecutionLog

- 接收规范 Operation、commentary、delegation 和 context-compaction 读模型；
- 过滤内部模型调用等不应面向用户的运行细节；
- 连续操作进入同一个折叠面板，展开后直接显示具体操作，不增加第二层“已操作”；
- 运行中显示状态和计时，终态显示步骤数和总耗时；
- 非手动旧状态不能让后续工具批次自动展开，用户手动选择保持稳定。

### TaskProgress

- 输入框上方胶囊是任务计划的唯一展示入口；
- 顺序执行显示当前步骤，并行执行显示完成数量；
- 最终输出结束后胶囊消失；
- LongTask 单元不进入执行日志或 Assistant 正文。

### Composer

- 统一文本输入、自动增高、Enter 提交、禁用、发送和终止；
- 模型选择、上下文用量和排队状态使用通用控件；
- 业务上下文选择放入受限的 `renderComposerLeading` 扩展口。

## 运行时职责

`src/agent-runtime` 拥有：

- `AgentConversationMessage`、任务计划、工具片段和会话运行状态类型；
- Canonical AgentOutputEvent 的实时与重放归约；
- chunk dispatch、commit scheduler、terminal handling 和 replay；
- 会话消息的历史转换和上下文预算投影；
- 当前会话的运行、排队、恢复和停止读模型。

它不拥有：

- React 组件；
- 业务上下文装配；
- 领域 Artifact 或 Revision；
- 模型密钥和产品设置页面；
- 面向用户的领域文案。

## 数据流

### 输出

```text
PurrA AgentOutputEvent
  -> services SSE
  -> agent-runtime canonical reducer / replay
  -> AgentConversationController state
  -> AgentConversationPanel
  -> Message / ExecutionLog / TaskProgress
```

实时事件和页面刷新后的重放必须生成相同的面板读模型。组件不得根据事件到达时间重新
推测 Operation 状态、耗时或输出顺序。

### 输入

```text
AgentConversationPanel
  -> Controller action
  -> 业务适配器
  -> services / 领域命令
  -> PurrA Run
```

面板只调用 `send`、`abort`、`editMessage` 等语义命令，不了解剧本阶段命令或书籍章节
绑定的具体 wire payload。

## 错误、终止和恢复

- 连接失败、Run 失败、用户终止和暂停使用不同的结构化状态，不写入 Assistant 正文；
- 已收到的公开 Provider 文案可以保留，宿主不得补写总结；
- 手动终止必须结束活动计时，但不删除已完成操作；
- 重放和恢复沿用相同 reducer，不能创建第二套“历史展示”逻辑；
- 领域附件失败不应破坏已经提交的 Assistant 输出，附件自行呈现领域错误；
- Controller 适配失败由面板通用错误边界呈现，并保留可诊断标识。

## 迁移方案

### 阶段一：建立通用运行时边界

1. 将消息类型、chunk handlers、history conversion、runtime store 和任务计划选择迁入
   `src/agent-runtime`；
2. 更新现有调用方；
3. 添加边界测试，禁止 `agent-runtime` 依赖 `Workspace/AiPanel`。

阶段一只改变所有权，不改变协议和运行行为。

### 阶段二：迁移通用消息与执行组件

1. 将 Assistant 输出、执行日志、工具状态、子 Agent、审批、结构化问题、错误展示和
   Markdown 移出 `Workspace/AiPanel`；
2. 将书籍专用 `SettingDiffCard` 改为领域附件；
3. 让书籍和剧本路径共享相同消息组件；
4. 删除共享组件对业务目录的导入。

### 阶段三：建立完整 AgentConversationPanel

1. 组合 `ConversationIndex`、`ConversationViewport`、`Composer` 和 `TaskProgress`；
2. 定义最小 Controller 与 Extensions 契约；
3. 先接入剧本 Agent，验证持久化重放、Artifact 附件和终止恢复；
4. 再接入书籍 AI，验证虚拟长列表、编辑、收藏、上下文和记忆入口。

### 阶段四：删除重复实现

- 删除 `Workspace/AiPanel/components/ChatMessageList` 中被替代的列表、气泡和消息体；
- 删除 `Workspace/AiPanel` 对共享 runtime 类型和 handlers 的所有权；
- 删除只为兼容反向依赖存在的 re-export；
- 保留书籍 AI 的 Controller、上下文、记忆、收藏和 Prompt 模板组合。

## 兼容与提交策略

- 每个阶段必须保持书籍 AI 和剧本 Agent 同时可运行；
- 使用临时 re-export 时必须在同一迁移阶段删除，禁止长期双入口；
- 不复制组件后再逐步修改；文件迁移后立即切换所有调用方；
- 不改数据库和后端 wire schema，因此无需数据迁移；
- 既有执行面板修复必须随通用组件迁移保留，并由共享测试覆盖。

## 测试与门禁

### 结构门禁

- `src/agent-runtime/**` 不得导入 `Workspace/AiPanel`、`ScreenplayAgentPage` 或小说业务；
- `src/components/AgentConversation/**` 不得导入任何业务页面；
- 业务模块不得重新声明 `AssistantTimeline`、`WorkLog`、`ChatMessage` 或任务胶囊逻辑；
- 共享组件只能通过 `src/purr-components` 使用基础 UI，不直接引入新的 UI 框架。

### 行为测试

- 实时归约与零游标重放得到相同消息；
- 普通书籍对话和剧本对话渲染相同的 Assistant 输出结构；
- 内部模型调用不出现在执行日志；
- 连续工具调用默认收起，展开后只有一层具体步骤；
- 用户手动展开状态不被流式更新重置；
- 顺序和并行任务胶囊分别显示正确口径；
- 最终输出完成后任务胶囊消失，执行摘要保留；
- 终止结束活动计时并保留已完成步骤；
- 领域附件不会污染 Assistant 正文或执行日志；
- 编辑、复制、会话切换和长对话滚动行为在两个现有入口一致。

### 验证命令

实施阶段至少运行：

```bash
npm run typecheck
npm run test:unit
npm run check:agent-refactor
```

涉及 Python Agent Core、事件协议或持久化的改动仍需完整后端门禁；单纯文件迁移不得借机
修改这些层的行为。

## 验收标准

1. 书籍 AI 与剧本 Agent 都渲染同一个 `AgentConversationPanel`。
2. 小说 Agent 接入时无需复制消息、执行日志、任务胶囊或输入器。
3. `src/components/AgentConversation` 与 `src/agent-runtime` 对业务目录零依赖。
4. `Workspace/AiPanel` 只剩书籍 AI 的 Controller 和领域组合，不再拥有通用消息组件。
5. 现有执行面板、模型操作过滤、计时、折叠和任务胶囊行为全部通过共享回归测试。
6. 书籍 AI、剧本 Agent 的发送、终止、恢复、编辑、复制和会话导航通过只读 UI 验证。

## 明确取舍

- 选择“通用面板 + 业务 Controller”，因为它同时消除 UI 重复和运行时反向依赖；
- 不选择只移动文件，因为那不会改变实际所有权；
- 不选择 Headless 插件框架，因为当前只有少量稳定领域扩展，Controller 和三个插槽足够；
- 优先复用现有组件和状态机，不重做视觉设计，不引入新依赖。
