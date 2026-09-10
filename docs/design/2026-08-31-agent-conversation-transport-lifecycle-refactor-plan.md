# 三类 Agent 对话链路与接口生命周期改造计划

> 日期：2026-08-31。状态：代码修复已实施；当前验收记录和剩余验证边界见第 9 节。
> 范围：小说来源分析、小说写作 Agent、剧本 Agent，以及它们共用的订阅、快照、恢复和调试链路。
> 依据：2026-08-31 代码基线。下文时间间隔是代码配置，不是实测 QPS；本文不代表真实 Provider 或浏览器性能验收已通过。

## 1. 结论与边界

问题不只是“某个接口轮询太快”，而是实时订阅、历史恢复、业务结果刷新和开发调试分别维护了读取循环，缺少一致的所有权与退出条件。

已经抽离的部分包括公共 Agent 事件协议、事件持久化、消息 reducer、执行过程展示和快照重放。尚未完整统一的是：谁建立订阅、谁恢复、何时读快照、何时停止，以及业务结果如何通知刷新。因此不能认定“完全没有抽离”，也不能认为“使用同一个对话组件就已经完整接入”。

修复前三类 Agent 的问题不同：来源分析缺少页面流式消费；小说写作主要是恢复链路重复读取；剧本已有 SSE，但仍存在逐批刷新完整快照及并行轮询。剧本的展示规范可以复用，旧传输实现不能直接作为无缺陷模板复制。

本次目标：复用已有协议和工具，补齐一套共同的传输生命周期；保留三个业务域各自的命令、状态权威与结果查询。不是重写 Agent Core，也不是再建一套平行对话框架。

## 2. 修复前基线（保留用于对照）

### 2.1 三条链路对照

| 场景 | 当前主链路 | 额外读取 | 判断 |
| --- | --- | --- | --- |
| 来源分析正常执行 | POST 返回受理回执；页面轮询任务，再从 Run 快照重放对话 | 活跃/等待受理任务时，每轮等待 1500ms 后一起读 `analysis-runs` 和 `analyses`；进度变化触发运行中 Run 全量重放 | 主链路需要改成增量订阅；进度与已保存结果必须拆开 |
| 小说写作正常执行 | `/api/ai/chat/stream` 提供实时流；公共 reducer 消费 | 工具成功/终态时读取提案投影 | 已有流式能力；提案读取有业务用途，不能直接删除 |
| 小说写作断线/刷新恢复 | 流中断后增量快照兜底；新页面恢复未完成 Run 时循环重建 | 兜底等待 500ms；新页面路径存在无 `after` 读取后再次全量 hydrate | 保留恢复能力，消除重复全量读取，恢复后回到订阅 |
| 剧本正常执行 | SSE 返回 canonical chunks，公共 reducer 消费 | 每批新 chunks 都使完整 Conversation Snapshot 失效；80ms 调度合并刷新；另有 2s/10s 活跃/非活跃兜底定时器 | 流式已接入，但“读到增量后再读全量”和健康连接下的定时刷新冗余 |
| 来源分析/剧本开发调试 | 另起 Run snapshot 读取循环 | 无新页时等待 200ms；剧本可监视多个关联 Run；不以面板展开为前提 | DEV 专属额外负担；应共用事件消费，不应额外追踪同一实时数据 |
| 剧本 SSE 服务端 | 每个连接循环增量查询 journal | 空闲循环等待 40ms；没有根据当前任务结束退出的分支 | 是后端数据库读取，不是前端 HTTP 轮询；也需要单独治理 |

上述定时器不代表每个请求都严格按该频率发出：请求耗时、事件触发、合并和分页都会影响实际请求量。生产与 DEV 必须分别测量。

### 2.2 接口不是“没有作用”，而是调用职责混在一起

- `GET /api/ai/agent-runs/{runId}` 是只读执行快照，包含 Run、计划、公开事件、业务投影等；读取不会重新调用模型。没有 `after` 时从头读取事件第一页；恢复辅助函数还可能继续拉完全部分页。
- `GET /api/novel-source-revisions/{revisionId}/analysis-runs` 当前负责任务状态、关联 Run、进度和结果引用发现，现阶段不能直接撤掉。
- `GET /api/novel-source-revisions/{revisionId}/analyses` 读取已发布分析，当前服务端返回最新保存版本，并装配事实、技法和证据；它不是实时生成中的结果。
- 新生成、待审核的结果由 `GET /api/novel-analysis-artifacts/{artifactId}` 读取。生成完成与用户保存/发布是两个不同生命周期。
- 根 Run 的快照暂时不变，不等于任务没执行：子 Run 可能正在产出。连续相同响应能证明该时段重复读取没有增量收益，不能单凭这一点判定整个任务无活动。

任务计划、工具状态、公开模型输出应由同一实时事件链路驱动；完整业务产物、审批内容不必塞进公共流。这里需要统一“事件与查询的分工”，不是把所有 HTTP GET 都替换为 SSE。

### 2.3 问题清单与代码依据

优先级含义：P1 为本次必须完成的正确性/主链路收口；P2 为随后完成的冗余读取与资源治理，不代表可以永久保留。

| 编号 | 优先级 | 要改的内容 | 当前依据 |
| --- | --- | --- | --- |
| C1 | P1 | 建立统一订阅所有者、恢复状态、游标和清理规则 | 三个页面及 `backendApi.ts` 各自维护读取与恢复循环；共同 reducer 并不管理完整传输生命周期 |
| N1 | P1 | 来源分析及分析追问接入公开事件订阅，覆盖根/分片/恢复产生的 Run | [路由](../../backend/routers/novel_sources.py)返回 202；[服务](../../backend/application/novel_analysis_service.py)后台消费 `_runs.run`；[页面](../../src/NovelSourcesPage/index.tsx)依赖轮询和重放 |
| N2 | P1 | 拆开任务进度、待审核产物、已发布结果的读取 | `NovelSourcesPage/index.tsx::reloadAnalysis` 同时请求 `listAnalysisRuns` 与 `listPublishedAnalyses` |
| N3 | P1 | 运行中只追加事件，停止在进度变化时从头重放所有活跃 Run | [analysisConversation.ts](../../src/NovelSourcesPage/analysisConversation.ts)::`loadNovelAnalysisConversation` 只缓存已结束 Run；[runSnapshotHydration.ts](../../src/agent-runtime/runSnapshotHydration.ts)::`loadCompleteAgentRunSnapshot` 每次从第一页开始 |
| N4 | P1 | 来源/版本切换后，旧请求不能覆盖新页面状态 | `reloadAnalysis` 内直接更新 state；调用方的 `disposed` 检查发生在其返回之后。需在提交状态前校验作用域并补竞态测试 |
| W1 | P1 | 小说写作刷新恢复改为一次基线加增量，避免同页重复读 | [AiPanel/index.tsx](../../src/Workspace/AiPanel/index.tsx)运行中恢复循环无 `after` 读取；[bookConversationHydration.ts](../../src/Workspace/AiPanel/bookConversationHydration.ts)::`hydrateLatestBookRun` 在提供查询依赖时再次全量加载 |
| W2 | P1 | 断线兜底统一退避、错误分类和重新订阅 | [durableAgentStreamRecovery.ts](../../src/services/durableAgentStreamRecovery.ts)已有增量游标，但进入后持续循环至终态/中止；[backendApi.ts](../../src/services/backendApi.ts)注入固定 500ms 等待 |
| W3 | P2 | 提案只在相关业务投影变化时补读，保留审批与去重 | [bookProposalProjection.ts](../../src/Workspace/AiPanel/bookProposalProjection.ts)用 `limit: 1` 读取 `productEvents`，触发覆盖所有成功工具/操作；已有请求合并和提案去重 |
| S1 | P1 | 剧本文本/过程增量不再使完整业务快照失效 | [conversationClient.ts](../../src/ScreenplayAgentPage/conversationClient.ts)::`watch` 每次调用 `onChunks` 后都调用 `onInvalidate` |
| S2 | P1 | 剧本 HTTP 兜底由连接状态控制，不与健康 SSE 常驻并行 | [ScreenplayAgentPage/index.tsx](../../src/ScreenplayAgentPage/index.tsx)订阅 effect 的 `finally` 持续调度 2s/10s 快照读取；没有以 SSE 健康状态为开关 |
| D1 | P2 | 调试面板观察现有消费链路，删除同一 Run 的独立高频循环 | 两个页面 DEV snapshot 监视；剧本同时已经对 SSE chunks 调用 `recordAiDebugChunk` |
| B1 | P2 | 约束服务端空闲 journal 读取与订阅者资源 | [screenplay_conversations.py](../../backend/routers/screenplay_conversations.py)的 `_STREAM_POLL_SECONDS = 0.04`；[快照仓储](../../backend/infrastructure/persistence/sqlite_screenplay_agent_repository.py)::`get_snapshot` 还会装配会话所有 Turn、Operation 和 Task units |
| G1 | P1 | 更新规范和测试，避免旧“每批事件都刷新快照”规则继续固化 | [剧本 API 文档](screenplay-agent-api-v2.md)仍描述 cursor event 仅作失效通知，代码已传 canonical chunks；现有行为测试不等于请求量验收 |

N4 是代码中已存在的失效检查缺口；实际是否发生串页、发生频率多高，需用延迟响应测试验证，本文不宣称已经完成浏览器复现。

## 3. 统一后的职责

### 3.1 保留已有公共层

- 后端复用 [agent_run_service.py](../../backend/application/agent_run_service.py)、[sse_mapping.py](../../backend/application/sse_mapping.py)、[agent_run_queries.py](../../backend/application/agent_run_queries.py) 与持久化 output journal；借鉴 [screenplay_agent_stream.py](../../backend/application/screenplay_agent_stream.py) 的有作用域游标查询。
- 前端复用 [chunkReplay.ts](../../src/agent-runtime/chunkReplay.ts)、[canonicalOutput.ts](../../src/agent-runtime/canonicalOutput.ts)、[runSnapshotHydration.ts](../../src/agent-runtime/runSnapshotHydration.ts) 和 [durableAgentStreamRecovery.ts](../../src/services/durableAgentStreamRecovery.ts)。公共 [AgentConversation/Panel.tsx](../../src/components/AgentConversation/Panel.tsx)继续负责展示。
- 在这些入口上补订阅/恢复所有权，不再让页面复制循环；是否需要新增小模块由实际依赖决定，不预先规划一组框架类。
- 各域保留命令与结果映射：来源分析 Task/Artifact、小说写作 Conversation/提案、剧本 Turn/Operation/Revision。不得为了统一传输，把三者硬改成同一业务状态机。

### 3.2 命令、事件、快照、产物各司其职

| 类型 | 职责 | 约束 |
| --- | --- | --- |
| 提交命令 | 持久化受理回执并启动/恢复一次业务执行 | 连接重试不能重新启动模型；使用原有幂等键、Run/Turn/Task 关联与版本校验 |
| 公开事件订阅 | 增量传输公开回复、执行过程、计划变化、运行状态和必要业务引用 | 与历史重放使用同一协议；不给客户端复制一套权威业务状态机 |
| 快照 | 首次进入、历史恢复、游标失效或明确需要的状态校准 | 不作为健康流下的周期进度来源；已有完整数据不能被辅助函数再取一次 |
| 业务变更通知 | 指明哪个已持久化的投影/资源版本发生变化 | 按资源与版本合并；文本 delta 不触发完整业务刷新；不能早于结果可读时发布 |
| 产物查询 | 按需读取分析详情、原文证据、提案、Revision 等 | 保留领域授权；不把私有 JSON、原文或完整提案正文广播到公共流 |
| 调试观察 | 展示同一订阅与恢复过程的事实 | 默认不另建高频读取；确有私有诊断需求时，显式操作、按需读取并脱敏 |

JSON 作为机器事件格式没有问题；禁止的是把内部 JSON 或宿主拼接的“正在分析……”伪装成模型回复。连接状态、实际工具状态仍可用公共组件的状态标签展示。

## 4. 生命周期规则

以下是目标传输状态，不是新增的 Run/Task 业务状态：

```text
进入作用域 → 加载基线与游标 → 补齐事件 → 实时订阅
                                 ↑          │
                                 └─ 退避恢复 ← 连接异常/事件缺口

业务结束 → 补齐最后事件与结果投影 → 停止该次执行的追踪
离开作用域 → 退订、停止本地恢复/定时器、废弃迟到响应
显式取消 → 调用业务取消命令 → 等待权威结果，不以断流冒充取消成功
```

1. **一个逻辑所有者。** 同一渲染进程、同一业务作用域的实时与恢复读取由一个所有者管理；消息、计划、结果入口、调试面板订阅它。需要多个底层子 Run 读取时仍由该所有者协调，不由各组件各读一份。不要求本轮实现跨浏览器标签页选主。
2. **订阅与兜底互斥。** 健康 SSE 下不启动周期快照。断线后先从已消费游标补齐，再重新订阅；采用有上限的退避和明确重试预算。预算耗尽显示连接异常/手动重连，不无限固定频率请求，也不擅自把服务端任务判失败。
3. **连接健康不等于模型正在输出。** 长时间无文本不自动判定断线；使用传输错误、约定的心跳/活性信号判断。心跳只保活，不触发快照、业务刷新或模型回复。
4. **基线与增量不能丢事件。** 定义快照水位与订阅游标的衔接，覆盖“取快照期间产生事件”的竞态。事件至少一次送达可以接受，前端必须幂等消费。
5. **作用域隔离。** 页面切换、会话切换、来源版本切换、卸载、StrictMode 重挂载时，清理旧订阅及等待器；每个异步状态提交前校验 scope/generation。请求取消只是优化，迟到响应隔离才是正确性保障。
6. **控制面与传输面分离。** 页面关闭/断网只是失去订阅；暂停、取消、恢复仍走各域命令与权威回执。提交已受理但尚无 Run ID 时，通过命令回执恢复归属，不能误追上一轮 Run。现有同会话串行、队列及投影所有权保护必须保留。
7. **结束要先收口。** 单个子 Run 结束不代表整个分析结束；根 Run 的交接也不等于长任务完成。按各域权威状态补齐事件及最终结果投影，之后停止追踪。保留 `failed`、`blocked`、`paused`、`canceled` 等领域差异，不统一显示“已完成”。
8. **空闲会话与活动任务分开。** 无任务时可保留低成本会话订阅，或关闭后在新提交/重新进入时恢复，但不能保留高频空轮询。若要接收其他窗口提交的任务，必须有明确的会话级通知策略，不能关闭后默默漏掉。
9. **永久错误不无限重试。** 权限失效、对象删除、游标格式错误与暂时网络错误分别处理；合法长任务不因前端重试预算耗尽被取消。隐藏页/离线页的读取策略也须明确，重新可见/联网只触发一次协调恢复。

### 4.1 游标与父子 Run 的专门要求

- 当前 Run `after` 是 Run 内序号；剧本 `chunkAfter` 是会话查询游标。两者不能混用或直接比较。先定义订阅作用域和游标语义，再决定共用路由还是领域路由适配。
- 来源分析必须发现后续动态创建的分片 Run、恢复 Run 及其早期事件。仅订阅最初根 Run 不算接入完成。优先利用持久化关联和可重放查询，不依赖浏览器内的瞬时列表。
- 多 Run 展示不能只比较局部序号。统一顺序/去重标识，并使实时、刷新重放对同一事件集呈现一致；不能把时间戳排序当作新的业务因果权威。
- 覆盖超过 500 条的分页、重复页、重复终态、无公开内容但游标推进、`hasMore` 不推进等情况。游标不可用需明确重建基线，不允许静默漏事件或忙循环。
- 快照中的“最新状态”与事件分页位置可能不同。看到终态元数据后，仍需补齐截至结束水位的历史，再收口；业务结果尚未可读时采用有界协调，不恢复高频无限轮询。

### 4.2 规划阶段也必须有真实执行状态

统一传输不能补救生产者没有发出的事件。2026-08-31 的实际诊断中，根 Run 的规划耗时 106.5 秒，而在规划结束前只有公开的 Run 开始事件。规划的私有非流式模型调用不应靠伪造回复或增加轮询来掩盖。

- 三类 Agent 的初始规划和动态调整由本地 PurrA 0.5.0 Core 统一管理。`AgentComposition` 把同一个 `AgentOperationController` 交给 Core，`ExecutionProfile` 直接使用 `AgentPlanner`；宿主 `agent_planning.py` 和 `observe_planner` 包装已经删除，不再创建第二套开始、终态、计时或取消逻辑。
- Core 使用 `kind=planning` 表达完整规划阶段，稳定展示键只有 `agent.operation.planning`；`display.labelParams.revision` 区分制定计划和调整计划。阶段内的 `kind=model` invocation 是诊断子操作，公共时间线不重复显示。
- 参考 Planner 只通过受管流式 invocation 接收 `purra.planning-stream/v1`：零至十六条完整 `progress` JSONL 记录，随后恰好一条私有 `plan`。没有旧单 JSON、非流式 Planner 回退、协议猜测或宿主转接层。
- SQLite 输出流持久化 `output_protocol`、planning Run/operation/revision/attempt；公开 `planning.progress` 提交前必须匹配仍活跃的规划阶段，并逐字节核对同 invocation 已持久化的 Provider 记录及 UTF-8 span。原始进度记录必须先持久化，随后才发布公开投影。
- 公共时间线显示“制定计划”或“调整计划”、执行状态、耗时及 Provider 提供的短意图。运行中从持久化开始时间更新本地计时，结束后使用持久化时长；不发起网络轮询，也不把普通模型子调用列成业务步骤。
- 规划 JSON、思维链、原文及底层异常正文仍是私有输出。公开 `run.todos_updated` 只保留 title/status 与步骤 id/title/status/type；前端把 `planning.progress` 作为独立 typed projection，不从私有 plan bytes 或等待时长重建说明。
- 实时和历史读取消费同一已提交事件并按事件序号去重。旧 Run 没有规划流记录时不补造历史；测试中旧确定性 Planner fixture 只在测试 Provider 边界编码为当前 JSONL，不进入生产代码。

回归入口：`backend/tests/test_agent_planning.py`、三类 Profile 的 composition 测试、来源分析真实 Core/假 Provider 测试，以及公共 `AssistantOutput/timeline.test.ts`。必须覆盖尚未返回模型文本时可见、成功/失败/取消、动态规划、私有内容隔离与重复回放。

### 4.3 所有 Agent 的工具步骤必须具有业务语义

这是一条共享 Agent Profile / Operation 展示合同，不是 Planner 提示词。Planner 只决定调用哪个工具及其参数；展示名称由宿主在工具 Operation 启动前，根据已经校验的领域状态和本次参数生成。不得让模型额外输出步骤名称，也不得把展示规范、原文或产物正文塞入规划上下文。

- 每个 Agent Profile 注册的每个工具都必须同时提供稳定的 `schema.display_names["zh-CN"]` 回退名和 `operation_display_params` 动态投影。缺少任一项时，`AgentProfileRegistry` 在应用装配期失败关闭；请求级附加工具在创建 Core 时执行同一检查。
- 动态投影必须返回 canonical `display.labelParams.displayNames`。标签描述本次调用的具体业务目标，例如集、场、章节标题、交付物类型、检索词、人物或设定范围；不能只重复工具函数名。
- 领域拥有业务名称解析。Core 只在 Operation 启动前调用投影并持久化参数，不理解人物、章节或剧本交付物；共享前端把 canonical `displayNames` 视为权威，不再按工具名维护领域分支。
- 公开标签必须单行、简短、可重放，不包含正文、候选内容、凭据、内部数据库 ID 或无法向用户解释的技术键。只有不透明 ID 且宿主没有已授权名称映射时，使用“指定人物”“指定设定”等诚实回退，不伪造名称。
- 实时、刷新和恢复消费同一已提交 Operation 标签。旧 Operation 没有保存业务目标时不事后猜测；新 Agent 或新工具若没有完成标签合同，不得进入生产装配。
- 确定性测试至少覆盖：所有生产 Profile 全量注册检查、不同参数产生不同业务标签、敏感正文/内部 ID 不进入标签、执行期非法投影失败，以及前端实时与回放使用同一名称。

公共执行入口为 `application/agent_tool_presentation.py`；Writing、Screenplay 与 Novel Analysis 的差异只存在于各自领域投影器中。该合同与本节的 planning Operation 规则共同构成三类 Agent 的统一公开执行范式。

## 5. 接口调用策略

以下为用途约束；已实施的订阅 URL、游标和调用时机见第 9 节。

| 现有接口/入口 | 保留的调用时机 | 应移除或替换的调用 |
| --- | --- | --- |
| `POST /api/ai/chat/stream` | 小说写作首次提交及当前实时输出；保留受理语义 | 不用重新 POST 作为断线重连；已有 Run 应重新订阅/补读 |
| `GET /api/ai/agent-runs/{runId}` | 历史基线、受控恢复、必要的业务投影查询和显式诊断 | 活跃 Run 反复从头读；UI 与调试并行读同一事件；长期固定频率恢复 |
| `GET /api/novel-source-revisions/{revisionId}/analysis-runs` | 进入/切换版本的任务发现、受理协调、恢复及相关任务投影变更 | 健康订阅下周期读全部任务；为了发现进度而连带刷新结果 |
| `GET /api/novel-source-revisions/{revisionId}/analyses` | 进入/切换版本、保存发布成功、明确的跨窗口结果变更或手动刷新 | 分析期间每 1500ms 跟随任务状态读取 |
| `GET /api/novel-analysis-artifacts/{artifactId}` | 新产物引用/版本出现、打开结果或审核后需要刷新 | 相同引用版本未变化时随过程事件重复取全文 |
| `GET /api/screenplay/v2/projects/{projectId}/conversation/snapshot` | 会话基线、恢复、相关 Turn/Operation/Revision 变更后的合并校准 | 每批文本 chunks 后重取；健康/空闲时常驻 2s/10s 读取 |
| `GET /api/screenplay/v2/projects/{projectId}/conversation/events` | 带游标重放与实时订阅，逐步接入共同生命周期 | 每个连接空闲时始终每 40ms 查库；事件页无差别触发快照刷新 |
| 小说写作提案投影读取 | 已落库的提案变更通知、终态必要收口；按提案 ID/版本去重 | 所有成功只读工具也一律查询；扩大读取为完整历史恢复 |

“减少前端请求”与“减少后端数据库查询”是两项验收。服务端优先复用已有通知/订阅能力；若部署约束下仍需 journal 轮询，必须明确空闲退避、查询共享或速率上限和退订释放策略，不承诺接入 SSE 就自然实现零轮询。

## 6. 实施顺序与交付物

### 阶段一：锁定契约与回归基线（C1、G1）

- 明确三类业务作用域、游标、受理回执、父子 Run 归属、终态与业务投影版本的关系。
- 为现有三条链路补请求计数、重复读取和异常场景测试；分别记录生产与 DEV 的请求数、传输字节、服务端 journal 查询数及最大并发订阅数。
- 定义共同订阅/恢复入口与错误分类，明确浏览器/Web/Electron 对应 transport adapter，核对所有调用端而非只改 Web 页面。
- 同步修订 [剧本重构章程](purra-screenplay-refactor-charter.md) 和 [API 文档](screenplay-agent-api-v2.md) 中“事件只作快照失效通知”的旧描述；保留业务权威与断线不取消原则。[旧架构文档](../agent-architecture-final.md)已经标明历史状态，不能把其“断开即取消”正文当作现行规则。
- 交付：契约、可复现的请求基线与失败测试。没有基线不先承诺百分比降幅。

### 阶段二：公共生命周期 + 剧本纵向收口（C1、S1、S2）

- 在既有快照/恢复/公共 reducer 上实现单所有者、游标推进、订阅状态、退避重连、终态收口及清理。
- 先用已有 SSE 的剧本链路验证契约：文本与过程直接增量更新；只有明确业务变更使对应投影失效；删除与健康连接并行的兜底。
- 调整 `conversationClient.ts`、页面订阅 effect、`backendApi.ts` 和必要的服务端事件适配；不把领域判断塞进公共 reducer。
- 交付：剧本不再逐批拉完整快照，并形成另两类可直接接入的共同入口。先在已有流上验证，再接来源分析，避免三处各做一版。

### 阶段三：来源分析完整接入（N1–N4）

- 后端提供可恢复的作用域订阅，纳入根/分片/恢复 Run、计划、真实工具过程、分析追问和产物变更引用。
- 页面改为基线加增量；拆分 `reloadAnalysis` 的进度、产物、已发布结果职责；补迟到响应隔离。
- `analysisConversation.ts` 保留必要的历史重放；删除运行中反复完整 hydrate 的主链路及被替代的定时器。
- 验证多分片、暂停、恢复、取消、无公开模型文本、结果待审核及已保存状态。无模型正文时展示真实过程/状态，不补假回复。
- 交付：分析执行过程直接流式可见；`analysis-runs`/`analyses` 不再作为常驻实时组合轮询。

### 阶段四：小说写作恢复与提案收口（W1–W3）

- 正常发送保留现有受理与实时能力，但接入同一生命周期所有者；刷新、EOF、异常恢复进入同一条恢复路径。
- `hydrateLatestBookRun` 可消费已取得的基线，不再“传入 snapshot 后又完整重取”；运行中按游标增量应用，恢复健康后重新订阅。
- 保留持久化 Conversation 收口、请求回执、会话队列与投影所有权校验；不得让旧 Run 的迟到刷新覆盖新一轮。
- 提案继续通过领域投影生成审批卡，改为相关变更通知触发、按 ID/版本去重；不为减少 GET 而广播私有 effect 全文。
- 交付：正常流、刷新恢复和断线恢复使用一致的消费规则，提案在实时/刷新后表现一致。

### 阶段五：调试、后端空闲成本及最终清理（D1、B1）

- `AiDevInspector` 使用共同事件消费与恢复记录；移除来源分析和剧本页的重复 200ms 调试循环。补读私有诊断须显式、有限且有权限边界。
- 实测并处理服务端 40ms 空闲查询，确定心跳、重试退避、空闲订阅及查询预算；验证订阅者数量增长后的资源释放。
- 删除已失去调用方的循环和旧适配，更新测试及运维说明，不保留长期新旧双轨。
- 交付：三类 Agent 联合回归、生产/DEV 请求对照和遗留项记录。每条迁移链路在删除原有读取前必须通过下面的正确性门槛。

## 7. 验收标准

### 7.1 可计数的网络与资源门槛

- 在作用域不变、SSE 健康、无相关业务投影变化的测试中：持续文本/过程输出引起的额外完整快照 GET 为 **0**。
- 已发布分析不变的整个分析执行期间：`analyses` 的周期性请求为 **0**；初始化、用户保存/发布等明确动作单独计数。
- 每个作用域只有 **1 个逻辑传输所有者**；健康订阅期间同数据的周期 HTTP 兜底为 **0**；调试面板打开/收起不新增独立实时读取循环。
- 历史每页只为一次恢复消费；不会“刚读取一页，又由 hydrate 从第一页重取”。分页补齐和有界业务结果协调不算无用请求，但必须记录原因。
- 结束/卸载/切换后不遗留该执行的请求循环，迟到响应不写入新作用域；后台任务是否继续由业务控制权决定。
- 服务端空闲查询数、每连接成本和重连预算在基线阶段明确并写入自动化断言，不仅凭浏览器请求变少验收。

### 7.2 必须覆盖的正确性场景

| 场景 | 必须保持 |
| --- | --- |
| 正常长任务、模型长时间无文本、批量 delta | 计划与真实工具过程可见；不因沉默取消任务，不拼接假回复 |
| 初次加载与实时输出交错、超过 500 条事件、重复/空页 | 无遗漏、无重复展示、游标单调推进；异常分页不忙循环 |
| 根 Run 交接、动态子 Run、失败重试/恢复 Run | 归属正确、早期事件可补、子任务完成不提前结束整次分析 |
| SSE 断线、HTTP 失败、EOF 早于终态、后端重启 | 从持久化事实恢复，不再次提交生成；可重试与永久错误分开 |
| 受理回执先到但 Run 未出现、提交响应丢失 | 绑定本次命令，不误用上一轮 Run；重复提交不产生额外任务 |
| 取消/暂停与结束竞态、取消时仍未取得 Run ID | 显式控制命令与回执有效；不把关闭连接当作取消成功 |
| A→B→A、版本切换、卸载重挂载、隐藏/离线恢复 | 旧结果不串页、订阅不叠加、恢复协调只启动一次 |
| 终态事件与产物投影提交先后、随后立即发下一轮 | 产物可读后通知；旧协调不覆盖新 Run；队列/审批所有权不退化 |
| 刷新前后、实时与历史、生产与 DEV | 同一公开事实集的结果一致；内部 JSON/凭据/私有产物不泄漏 |

### 7.3 测试入口与验证边界

- 前端扩展现有 `conversationState.test.ts`、`durableAgentStreamRecovery.test.ts`、`bookConversationHydration.test.ts`、`bookProposalProjection.test.ts`、`analysisConversation.test.cjs` 和公共 replay/lifecycle 测试；加入受控时钟、请求计数和延迟响应。旧测试中“每批事件都 invalidate”的预期应随新契约修改。
- 后端扩展 `test_agent_run_queries.py`、`test_novel_analysis_conversation.py`、`test_screenplay_agent_routes.py` 等，覆盖作用域校验、可重放订阅、退订不取消、结果通知提交顺序和空闲查询成本。
- 每阶段运行受影响测试；最终运行 `npm run check`、`npm run build:web`、`git diff --check`。直接运行后端测试时使用仓库 `.venv/bin/python -m pytest`。
- 最终在实际 Web/Electron 对应入口验证网络时序和展示。确定性测试、浏览器假 Provider 验证与真实 Provider 验证分别记录；未经运行不能把前两者描述为真实模型端到端通过。
- 修复验证记录见第 9 节；本轮没有新增真实模型调用。验证启动的临时服务须结束并确认释放端口。

## 8. 非目标、风险与完成清单

本次不重新设计对话 UI，不改变事实/技法/故事背景及提炼业务，不重新规划超长原文分段策略，不清理历史业务数据，不升级或发布 PurrA 包。长文本任务只作为多 Run、长时间执行及恢复的验收场景。只有宿主无法通过现有公共契约完成订阅时，才另列具体 Core 缺口，不默认扩大到 PurrA 重写。

需要警惕的风险：

- 过早删除快照会破坏历史恢复、状态权威和审批产物；必须先证明替代链路完备。
- 仅把 200ms/500ms/1500ms 改大，会降低请求频率，却不解决多个所有者、重复全量读取及串页。
- 只通知 Run 公共事件而漏掉无模型输出的 Task/Operation/Artifact 变更，会导致计划或结果卡永久不更新。
- 新增 SSE 而保留旧轮询，会同时付出两套成本；前端变流式而后端仍逐连接密集查库，也不算收口。
- 统一传输不等于统一所有业务状态；来源 Task、剧本 Operation、写作提案的发布/审批权限必须继续由各自业务域控制。

实现沿用既有公开事件日志、业务状态存储和进程内提交通知，没有引入新的 Core 协议或依赖。具体预算和限制见下一节。

- [x] 汇总当前差异、问题、代码入口和改造边界。
- [x] 阶段一：锁定契约，补充确定性回归与请求计数；未声称完成全量性能基准。
- [x] 阶段二：公共生命周期与剧本收口。
- [x] 阶段三：来源分析流式接入及查询拆分。
- [x] 阶段四：小说写作恢复与提案收口。
- [x] 阶段五：移除重复调试读取、降低空闲查询频率、完成联合自动化回归。
- [ ] 扩展验收：三类页面真实 Provider、Electron、生产/DEV 的完整性能及离线/后台恢复矩阵。

完成标准不是“文件已经抽出来”或“页面能看到文字”，而是三类 Agent 的实时、恢复、业务结果与调试都遵守同一组生命周期规则，并有测试证明没有遗漏、重复执行和冗余常驻读取。

## 9. 本轮实现与验收记录

### 9.1 已落地的入口

| 层/业务 | 实现 | 现在的职责 |
| --- | --- | --- |
| 公共前端传输 | `src/services/agentEventStream.ts` | 一个 fetch SSE 读取器拥有游标、退避、重连和 AbortSignal；先消费再提交游标，按同一 URL 续读，不提交新生成命令 |
| 公共后端订阅 | `backend/application/agent_event_stream.py` | 从持久化日志补齐分页，等待已有 publisher 的提交通知；只发送新增事件或业务版本变化 |
| 来源分析 | `GET /api/novel-source-revisions/{revisionId}/analysis-events?after=…&limit=500` | 版本范围内根/动态分片/恢复 Run 的公共事件；初始及变化时携带 `runs`、`publishedId`；游标是该查询使用的事件表 ID，不是 Run 局部 sequence |
| 剧本 | 原 `conversation/events?sessionId=…&chunkAfter=…` | 公开 chunks 直接进入共同 replay；`projectionVersion` 变化才使业务 snapshot 失效，不因文本 delta 刷新完整 snapshot |
| 小说写作恢复 | `GET /api/ai/agent-runs/{runId}/events?sessionId=…&after=…` | 验证 Run/会话归属，使用 Run 局部序号补齐公共事件，终态分页耗尽后关闭；不广播私有提案 |

Web 与 Electron 的业务调用均经过 `backendApi.ts`；没有另建 Electron 专用轮询。写作首次提交仍保留原 POST 流和幂等受理回执，已绑定 Run 的 EOF/断线和重新进入页面均转入公共 GET SSE 读取器。

来源分析页面不再常驻轮询 `analysis-runs` 和 `analyses`；前者只保留显式命令后的状态协调，后者用于初始化、发布引用变化及显式发布操作。`NovelAnalysisConversationStream` 在内存中保存已消费事件，后续分片关联出现时重放对应事件，不重新查询所有 Run。分片 Run 绑定、状态和产物引用也参与业务版本计算，不能仅依赖 Task revision。

剧本删除了健康/空闲时的 2s/10s HTTP 兜底和独立 DEV 诊断循环。业务刷新仍有 80ms 合并；必要 snapshot 失败最多协调重试 5 次。投影版本不包含 Assistant 正文、Provider usage 或 heartbeat 时间戳。

写作恢复复用已经取得的首个 snapshot，仅补剩余分页；之后增量合并。提案仅在相关写工具成功或终态时定向补读，保留审批私有正文的独立查询和 Conversation 持久化收口。连接重试预算耗尽只提示连接异常，不伪造 failed/canceled Run，也不吞成一次成功。

调试面板观察相同公开流，来源分析与剧本不再各建 200ms Run 快照读取器；诊断详情/稳定性报告仍是独立、有界查询，不混入公开回复。

### 9.2 生命周期与资源预算

- SSE 暂时失败：首次请求加最多 8 次重试，等待从 250ms 指数增加到 10s；只有已消费游标推进才重置连续失败计数。403/404 等永久错误不重试。预算耗尽后提示错误，重新进入作用域可建立新连接。
- 页面/版本切换和卸载：AbortController 关闭读取及重连等待器；提交 UI 状态前再次检查作用域。来源详情返回库页也清空分析作用域，避免组件仍挂载时遗留订阅。
- 隐藏页不另开轮询或第二个恢复循环；现有连接保持到浏览器中断或页面卸载。重新进入建立一个新所有者。本轮未添加跨标签页选主、后台页暂停策略或独立的手动重连按钮。
- 服务端没有“零查库”：复用已有进程内 publisher；无通知时最多每 2s 做一次持久化补查，覆盖其他进程写入及只有业务状态变化的提交。每次补查含作用域/版本/日志等多条 SQL，不能把 0.5 次补查/秒描述成 0.5 条 SQL/秒。旧剧本空闲 40ms 查询已移除。
- publisher 通知是进程级，不是跨进程消息总线，也尚未按作用域共享查询；其他 Run 活跃时会唤醒连接重新筛选。完整多连接压力测试、CPU/字节与生产/DEV 性能基线仍待补，不承诺百分比降幅。
- 会话级剧本流和来源版本流允许在空闲时保留，用于发现其他窗口提交/发布；单个写作 Run 流在终态补齐后结束。关闭订阅不等于业务取消。
- 首次命令受理、幂等 POST claim、显式取消回执的既有重试语义未在本轮改写；它们不是新的健康流旁路轮询。后续若收紧命令重试预算，必须同时定义“受理/取消结果未知”的控制状态，不能直接判定失败。

### 9.3 验证证据与限制

- 自动化：`npm run check:agent-refactor` 通过（前端 431 项、后端 1741 项，含公共组件边界、TypeScript 和架构检查）；补充公共 SSE 重连/游标/Abort、无模型配置的历史重放、动态分片补入、写作恢复归属、提案定向刷新、永久连接失败不伪造终态等测试。
- 新的 `backend/tests/test_agent_event_stream.py` 使用真实 SQLite 验证公开事件范围、动态 Run、业务版本变化与文本无关、分页排空、通知竞态及 Run/Session 隔离。
- 浏览器使用隔离数据库及本机临时服务，无真实 Provider 请求。来源 A→库→B→库→A 能恢复对应内容；未配置模型也能读取历史。另一个进程追加的新 Run 和公开测试事件，在不刷新页面的情况下显示出来。
- 同源测试代理计数：每次来源详情进入只建立 1 个 `analysis-events` 连接、初始化读 1 次 `analyses`；观察窗口及追加公开事件时没有 `analysis-runs`、Run 完整 snapshot 或额外 `analyses` 请求。返回库页记录到原流关闭。React StrictMode 的初始化列表/详情双读单独计数，不视为常驻轮询。
- DEV 的诊断/稳定性查询仍可能在选中或终态时发生，测试中确实观察到了；它们没有周期循环，不能宣称所有诊断 HTTP 请求都被删除。
- `npm run build:web`：首次依赖打包被沙箱网络限制；允许读取锁定依赖后成功。没有升级 PurrA，仍使用 `purra==0.4.1`。`git diff --check` 通过。
- 本轮浏览器证据覆盖来源页面和事件传输，不代表真实模型生成、暂停/恢复全流程或完整 Electron E2E 已验证。写作和剧本的改动另由现有生产形状的受理/取消/审批/恢复测试及后端集成测试覆盖。
- 临时 18531/5187 测试服务及测试页已关闭，端口确认无监听。原有 18321/5174 服务未重启、任务数据未修改；新增后端路由需重启现有后端才会生效。

### 9.4 规划可见性补齐验收（2026-08-31）

- `npm run check:agent-refactor` 再次通过：前端 432 项、后端 1754 项，以及 TypeScript、公共组件和依赖边界检查。
- 新增规划回归 12 项：真实开始先于模型返回、成功/错误/取消/截止时间、动态规划、静态 planner 不被误声明为动态，以及三个产品 Profile 的真实 planner/假 Provider 等待场景。
- 来源分析集成回归在假 Provider 规划调用内暂停，确认版本订阅已经返回开始事件；释放后检查同一 operation 只有一个成功终态，私有规划 JSON 没有进入公共 chunks。
- 公共时间线测试覆盖初始/动态规划的成功、失败、取消、计时与重复回放，不以正文或推理内容充当进度。
- `npm run build:web` 通过；首次下载锁定依赖受沙箱网络限制，联网重试后成功。`git diff --check` 通过。未升级 PurrA，未修改模型设置。
- 此次未进行真实 Provider、浏览器或 Electron 手工验收；没有启动开发服务，没有重启用户现有后端（18321 的 PID 13980）。代码对重启后的新执行生效，不补造旧 Run 的规划事件，也不代表规划耗时已缩短。

### 9.5 PurrA 0.5.0 Planner 协议流接入（2026-09-01）

以下状态取代 9.3、9.4 在当时记录的 PurrA 0.4.1/旧 Planner 接线状态；前述条目作为历史验收快照保留。

- 删除宿主 Planner 生命周期包装，三个产品 Profile 均直接使用 PurrA `AgentPlanner`；Core 是 planning operation 的唯一所有者。生产代码没有旧 JSON、非流式 Planner 或双 operation 兼容分支。
- SQLite schema、stream identity、Provider 原始字节验证、公开投影、会话重放和前端 typed timeline 已同步 `purra.planning-stream/v1`。规划流整体不能 promote 为 commentary，公开计划继续使用框架裁剪后的投影。
- `backend/tests` 全量通过；`npm run typecheck`、431 项 `npm run test:unit`、`npm run check:purr-components` 和 `npm run build:web` 通过。Web 打包实际从同级源码构建并安装 `purra-0.5.0` 与 `purra-mem0-0.5.0`。
- 本轮没有真实 Provider 请求，没有启动或重启用户后端，也没有浏览器/Electron 手工验收。因此当前证据证明确定性协议、持久化、重放和构建成立，不证明具体线上模型一定输出有用 progress，也不证明运行中的旧进程已加载新代码。
