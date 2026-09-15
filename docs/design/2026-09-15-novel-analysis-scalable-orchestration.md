# 小说分析 Agent 可扩展编排设计

> 日期：2026-09-15  
> 状态：完整模拟 Provider 纵切及重启/取消恢复门禁已完成；下一项为隔离真实 Provider 验收  
> 适用范围：replacement 小说分析 Agent；历史 legacy 数据继续只读  
> 核心目标：同一套编排既能处理短篇，也能处理数百万字长篇；分析按容量分片执行，但最终产物始终描述整部作品，而不是一组章节摘要。

## 1. 纠正容量口径

模型上下文窗口以 token 计量，不能把 `1M context` 解释为 100 万汉字，也不能推导出“40% 等于 400 万字”。中文字符、标点、数字、英文和 JSON 转义对应的 token 数都不同。

本设计把 40% 定义为强约束：

```text
单个 Map 请求中的来源正文 token 数 <= floor(所选模型 contextWindowTokens × 0.40)
```

字符数只用于产品展示。运行时准入、分片和重试均使用选定模型对应的 tokenizer；没有精确 tokenizer 时使用带版本号的保守估算器，并保留安全折扣。任何请求在发送 Provider 前都必须再次计算完整输入预算。

建议的单次 Map 请求预算是：

| 区域 | 最大/最小占比 | 说明 |
| --- | ---: | --- |
| 来源正文 | 最多 40% | 用户指定的硬上限 |
| System、工具 schema、任务合同 | 最多 10% | 不包含来源正文 |
| 已有摘要和必要依赖 | 最多 20% | Map 阶段通常接近 0 |
| 输出预留 | 至少 20% | 结构化分析与工具提交 |
| 安全余量 | 至少 10% | tokenizer 偏差、Provider 包装和修复轮次 |

如果固定开销或输出预留挤压了预算，正文预算只能继续缩小，不能借用输出预留。

## 2. 来源容量元数据

现有 `novel_source_revisions.character_count`、section 顺序、正文和 digest 可以保留，但需要补齐可审计的容量数据。

### 2.1 导入时持久化

每个 revision 保存：

- 总字节数、总字符数、section 数；
- 每个 section 的字节数、字符数、段落边界；
- 来源解析器版本和内容 digest。

每个 section 不强制等于章节。能识别章节时保留章节；无法识别时使用文档块。章节是优先切分边界，不是分析语义。

### 2.2 按模型缓存 token 指标

新增版本化 token metric，逻辑主键为：

```text
(sourceRevisionId, sectionId, tokenizerId, tokenizerVersion)
```

它保存估算/精确 token 数和计算时间。更换模型或 tokenizer 后重新计算对应指标，不原地覆盖旧值。Run 必须持久化实际使用的 tokenizer identity 和预算快照。

## 3. 两阶段规划

Planner 真正决定执行步骤，但 Host 先建立不可突破的容量和安全边界。

### 3.1 Host Admission Plan

Host 在调用 Planner 前生成只含元数据的 `SourceManifest`：

- revision identity 和 digest；
- 总字符数、总 token 估算、section/段落边界；
- 所选模型 context window；
- 40% 来源预算、输出预留和安全余量；
- 最少需要的 Map 分片数；
- 允许的最大模型调用数、并行度和总预算。

Host 同时生成不可变 `SliceManifest`。装箱规则是：

1. 按原文顺序把完整 section 装入当前 slice；
2. 下一个 section 会超预算时开启新 slice；
3. 一个完整 section 自身没有超预算时，不为了填满上一 slice 而拆开它；
4. 单个 section 自身超预算时，才按段落、句子、最终字符边界切开；
5. 无法识别章节时，把段落作为优先边界并使用同一装箱算法；
6. 每个字符范围恰好被一个 slice 覆盖，不丢失、不重复；
7. slice identity 包含 revision digest、起止位置、tokenizer 和预算版本。

算法采用保持原文顺序的 longest-prefix packing，而不是允许任意重排的通用 bin packing。
对不超过单片上限的章节，完整章节是不可拆分项；对超长章节，先产生有界子范围，再把
这些子范围作为有序项参与装箱。这样批次数量、每批章节数、字符数和 token 数都由同一
确定性脚本计算，Planner 无权自行调整。

```text
sourceTokenLimit = floor(selectedContextWindowTokens × 0.40)
```

如果只能使用估算 tokenizer，则 Slice Compiler 在该上限内应用版本化安全折扣；折扣后的
可用值是实际装箱上限，40% 仍是不可突破的外层硬上限。发送 Provider 前还要对完整序列化
请求复算一次，复算失败时重新缩小 slice，不能把超限请求交给 Provider 试错。

可选的邻接摘要不复制原文；如果确需重叠文本，必须单独计入预算并在覆盖计算中标记为 overlap，不能重复计入覆盖率。

### 3.2 Semantic Planner

Planner 读取用户目标和 `SourceManifest`，不读取整本正文。它输出可执行的 `AnalysisPlan`：

- 每个 Map slice 要提取的维度和结构化结果合同；
- 是否需要独立的角色、世界观、情节或技法 pass；
- Reduce 的归并键、冲突规则和 fan-in；
- 最终综合报告的章节结构；
- 质量检查项和完成条件。

Planner 的步骤会真正编译为 Durable Task DAG，不再只是 UI 映射。但 Planner 无权：

- 改变 revision、slice 范围或 40% 上限；
- 跳过任何正文范围；
- 无限增加 pass、调用次数或并行度；
- 修改工具权限、Artifact owner 或发布条件；
-要求已经从产品合同删除的原文引文/证据。

Planner 结果先经过 schema、容量、覆盖和成本校验；允许一次修复，仍不合法则在 Provider 调用前失败关闭。

### 3.3 分片生成时点

上传或冻结来源时只保存与模型无关的稳定指标，包括整本和每个 section 的字节数、字符数、
边界及 digest，并可缓存多个 tokenizer 版本的 token 指标。最终 `SliceManifest` 在用户启动
分析、选定模型与 context window 后生成，因为 32K、256K、1M 会得到不同的分片结果。

Planner 获得 `SliceManifest` 的统计和 locator，不获得正文：总分片数、每片字符/token 数、
完整章节范围、超长章节子范围、预计调用数和允许并行度。Map Agent 执行时才通过绑定的
`sliceId` 读取该片正文。

## 4. 可执行 DAG

```text
Root Run
  ├─ Admission: 固化模型、tokenizer、预算和 SliceManifest
  ├─ Planner: 生成并校验真正的 AnalysisPlan
  ├─ Map[1..N]: 每个 slice 一个独立模型请求
  ├─ Reduce[1..K]: 按预算分层归并 Map Artifact
  ├─ Synthesize: 形成整书人物、背景、设定、情节和技法
  ├─ Coverage Gate: 宿主校验全部 slice 已被消费
  └─ Review/Publish: 形成不可变待审核成果
```

### 4.1 Map

每个 Map operation：

- 绑定一个不可变 slice，而不是绑定“第几章分析任务”；
- 通过无模型参数的 `readNovelSourceSlice()` 一次读取 Host 为当前 Child 绑定的完整原文；
- Child 的 instruction、objective、input payload 和初始 messages 均不得包含正文；正文只能出现在该工具的返回消息中；
- 工具结果作为不可信 Tool/Resource Context，不能拼入 System 指令；
- 使用无历史的新模型请求，避免其他 slice 和旧对话占用窗口；
- 输出 `slice_analysis` Artifact，包含结构化事实、观察和该 slice 的覆盖 locator；
- 不保存原文引文或用户可见 evidence；locator 仅用于宿主覆盖、恢复和审计。

Map operation 使用独立 Child Run 或框架正式支持的独立模型调用所有权，不能让并行 operation 争抢同一个 Root Run 的 execution lease。并行度由 Provider capacity 和用户预算共同限制。

### 4.2 Reduce

Map 输出总量也可能超过上下文，因此不能一次性全部汇总。Host 按 reducer 输入预算对相邻 Artifact 装箱，Planner 的归并规则在每个 Reduce request 中执行：

- 合并人物别名、身份、状态和关系；
- 合并背景、规则、地点、势力和物品；
- 连接跨 slice 的事件、时间线、伏笔和未决情节；
- 去重局部技法，并区分局部现象与全书稳定风格；
- 显式记录冲突和未知项，不用后出现的结果静默覆盖前者。

Reduce 递归执行，直到剩余 Artifact 能在一个 Synthesize 请求中容纳。fan-in 由 Planner 提议、Host 按实际 token 预算裁定。

### 4.3 Synthesize 与 Coverage Gate

Synthesize 只消费最终 Reduce Artifact，不再重复读取五百万字原文。它输出整书层面的结果，不按 slice 或章节罗列摘要，并必须包含面向用户的非空 `summaryMarkdown`。

Coverage Gate 由宿主确定性校验：

- SliceManifest 中每个非 overlap 范围恰好有一个成功 Map Artifact；
- 每个 Map Artifact 的 revision/slice digest 与本 Run 一致；
- Reduce lineage 最终覆盖全部 Map Artifact；
- 必需结果字段存在且满足 schema；
- 没有把 canceled、旧 revision 或其他 Run 的结果混入。

只有 Coverage Gate 通过后才能生成 review Artifact 和发布结果。Review 成功时，Root 的
`finalResponse` 必须投影为经过审核的 `summaryMarkdown` 自然语言正文；Artifact URI、内部
状态文案或空字符串都不能充当分析完成后的最终回复。

## 5. Artifact 与恢复

Artifact identity 至少包含：

```text
(rootRunId, taskId, operationId, attempt, artifactKind, inputDigest)
```

- completed Map/Reduce 不因进程重启而重做；
- attempt 中断后先对账已提交 Artifact，再决定 finalize 或重试；
- 重试必须使用同一 slice 和同一 planner step；
- Planner plan、SliceManifest、预算快照和 lineage 全部持久化；
- 用户取消只停止未结算 operation，已完成 Artifact 保留但不得自动发布；
- 修改来源会产生新 revision 和新 Root Run，不能复用旧 coverage。

## 6. 成本和超长来源准入

在启动真实分析前展示并持久化估算：

```text
mapCalls = sliceCount × plannerMapPassCount
reduceCalls = 按 fan-in 逐层计算
totalCalls = planner + mapCalls + reduceCalls + synthesize + review
```

五百万字不应被承诺为“一定一次完成”。是否能完成取决于实际 token 数、所选模型窗口、Provider 请求时限、单次输出上限、总预算和并行容量。超过产品设定的调用数或费用上限时，应在创建 Durable Task 前要求用户确认或选择更大窗口/更低分析深度，不能启动后才耗尽预算。

## 7. 可观察性与产品呈现

普通对话只展示自然阶段说明，例如“正在分析下一部分内容”“正在归并跨段人物关系”，不持续刷章节编号、完成数量或内部 Unit 状态。

诊断面板可以展示精确数据：

- source token、slice 数和每个 slice 的 token 预算；
- Planner 最终 DAG 和被 Host 调整的原因；
- 每个 Map/Reduce Child Run、Provider 请求、工具调用和 Artifact；
- 首包时间、生成时间、超时、重试和 token usage；
- Coverage lineage 和最终门禁结果。

工具计数必须真实反映 `readNovelSourceSlice` 与 Artifact 提交，不能再出现“Host 暗中读取全文但工具调用为 0”的情况。

## 8. 对现行 replacement 的替换范围

以下现行设计不能继续保留为兼容分支：

- 删除固定 `analysis:work -> overview -> technique -> coverage -> review` recipe；
- 删除 Planner 的 `presentation_mapping_only` 权限；
- 删除把所有 segment 重新拼进一个 System Message 的 `sourceMaterial` 路径；
- 删除固定 16K segment 预算后又整本合并的矛盾实现；
- 为 Map/Reduce 建立独立执行所有权，允许受控并行；
- 保留 source revision/section 存储和历史只读 Run，但不保留双执行路径。

## 9. 实施批次

1. **容量元数据（已完成确定性实现）**：补 section metrics、tokenizer metrics 和导入/冻结回填；只做 additive migration。
2. **Admission 与 Slice Compiler（已完成确定性实现）**：实现 40% token 上限、完整覆盖和长 section 语义切分；不调用模型。
3. **Planner Contract（已完成确定性实现）**：让 Planner 生成真实 Map/Reduce/Synthesize DAG，并加入容量、成本和工具一致性校验。
4. **Map Child 执行（已完成模拟 Provider 纵切）**：接入 `readNovelSourceSlice`、独立 Run/Operation、slice Artifact、取消和重试。
5. **Hierarchical Reduce（已完成隔离实现与真实 Provider 验证）**：实现有界 fan-in、冲突归并、lineage 和恢复。
6. **Synthesize/Coverage/Review（已完成隔离实现与真实 Provider 验证）**：生成整书结果并用确定性覆盖门禁发布。
7. **切流与删除**：新 Run 只走新 recipe；真实 Provider、五百万字合成 fixture、Electron、重启恢复通过后删除现行固定 recipe。

## 10. 验收门禁

- 32K、256K、1M 三种窗口下，任一 Map 请求的来源正文不超过选定窗口的 40%；
- 章节齐全、无章节、单章超长三种来源均能形成无丢失、无重复的 SliceManifest；
- Planner 步骤真实出现在持久化 DAG，非法计划在调用 Map Provider 前被拒绝；
- Map 首次模型请求不含任何来源正文，正文只在 `readNovelSourceSlice` 的 Tool result 中出现；
- 五百万字 synthetic fixture 能完成 Map、分层 Reduce、Synthesize 和 Coverage Gate，测试不要求真实保存五百万字创作内容；
- 一个 Map 中断只重试该 slice，不重跑其他已完成 slice；
- 重启后从持久化 DAG 和 Artifact lineage 恢复；
- 普通 UI 不按章节展示分析结论，最终结果是整书人物、背景、设定、情节和技法；
- DONE Root 必须带非空的整书总结正文，不能只显示任务完成、进度或 Artifact URI；
- 真实 Provider 能看到一次 slice 读取工具和一个通过结构校验的最终结果，诊断计数与持久化事件一致；
- 完成 Electron 验收后停止所有测试进程，并确认开发端口无监听。

## 11. 已确认决策

| 日期 | 决策 | 约束 |
| --- | --- | --- |
| 2026-09-15 | 分多少批、每批多少章及具体字符范围全部由 Host Slice Compiler 确定 | Planner 只能消费 SliceManifest，不能修改范围或容量 |
| 2026-09-15 | 单片来源上限是所选模型 context window 的 40% | 以 token 为权威，字符数仅用于展示 |
| 2026-09-15 | 章节是优先物理边界，不是分析语义 | 未超限章节不拆；只有超长章节才按段落、句子切分 |
| 2026-09-15 | SliceManifest 在分析启动且模型窗口确定后生成 | 上传时保存稳定容量指标和可复用 tokenizer metrics |
| 2026-09-15 | Planner 不读取原文 | Planner 读取用户目标、SourceManifest、SliceManifest 和紧凑前置 Artifact |

## 12. 实施记录

| 日期 | 完成项 | 确定性证据 | 未覆盖边界 |
| --- | --- | --- | --- |
| 2026-09-15 | 为来源 section 增加 byte/character metrics，并增加按 tokenizer id/version 缓存的 token metrics；旧库只做 additive 回填 | 导入持久化、表结构、旧行回填不修改原文的定向测试通过 | 未在真实用户数据库执行迁移 |
| 2026-09-15 | 新增确定性 Slice Compiler：按40% token 硬上限贪心装箱，完整章节优先，只切分超限章节，生成可重现 slice identity 并校验连续完整覆盖 | 20 项定向测试通过；500 万字单 section 合成基线在 1M 窗口下生成 13 片，最大 399,997 token，覆盖终点 5,000,000，本机耗时 0.012s | 尚未接入 Planner、Provider 请求或生产新 Run；本机耗时不是跨环境性能承诺 |
| 2026-09-15 | 新增严格 AnalysisPlan schema 和 Host DAG 编译器：Planner 只决定语义 pass、Reduce fan-in、综合结构与质量检查；Host 按不可变 SliceManifest 展开 Map、分层 Reduce、Synthesize、Coverage 和 Review 真实 DAG | Planner 上下文无原文、schema 失败关闭、DAG 依赖、recipe digest、pass/模型调用预算拒绝均有定向测试；与现行 replacement 定向回归共 52 项通过 | 合同尚未接入生产 Profile，也尚未实现 Map Child 的 slice 读取和 Artifact 提交 |
| 2026-09-15 | Map Child 第一纵切：新增无模型参数的 `readNovelSourceSlice`，只能读取 Host 绑定 slice；Map executor 校验 pass/dimension/slice 合同、Child 的 Root/parent 归属、结构化 findings 和 attempt Artifact | 工具无法越界选 slice；Root-owned 执行失败关闭；模型输出错误可重试；错误归属不可重试；同 attempt Artifact 重放不再调模型 | PurrA `RecipeLongTaskDispatcher` 现行合同强制 Unit 属于 Root Run，因此仍需 Tree-aware dispatcher 通过正式 Run-tree command 创建/恢复 Child；本项未手工造生产 Child，未切流 |
| 2026-09-15 | Map Run-tree 命令适配：确认无需复制 Recipe dispatcher；Map runner 通过 AgentCore 公开 `spawn_agents`/`join_agent_runs` 创建和收取 Child。Child grant 只允许 `readNovelSourceSlice`、禁止继续 spawn，slice scope 放入 Run-tree 不可变 `input_payload`；工具按当前 `state.run_id` 回查 Host scope | 命令幂等 key 绑定 task/unit/attempt；只读 grant、冻结 input、Child JSON 结果和 Core 绑定有定向测试；与 composition/profile 扩大回归共 105 项通过 | 不能把 Map Child 工具直接装入现行 v1 Profile：v1 会对 Child 再次执行整书 Durable admission。下一项必须组装 v2 Profile，显式区分 Root Planner 和 Map Child reactive 请求；未切流 |
| 2026-09-15 | 隔离 v2 Profile 组装：新增 `novel_analysis.scalable.v2`，Root 在 Planner 前按选定窗口编译 SliceManifest，Planner context 无原文，且 Root 不获得 Map 来源工具；Map Child 强制 Reactive、只获得 `readNovelSourceSlice`、禁止再次 durable admission。v2 request scope 只保留 revision/command，不再携带 v1 16K segments。Profile 允许最多 4 个并行 Child，Root 容量受 Host 1,024 Agent 上限和 AnalysisPlan 512 模型调用上限双重限制 | Root/Child 模式隔离、无原文 Planner context、严格 AnalysisPlan admission、scalable recipe、唯一 v2 隔离 composition 及现行 v1/composition 回归共 109 项通过 | 尚未在真实 AgentCore 内用模拟 Provider 跑 Root→Child→read tool→Artifact；生产 registry/route 未改 |
| 2026-09-15 | 模拟 Provider Map 纵切：真实 AgentCore 从 Root Planner 进入 durable recipe，由 Host 创建独立 Child Run；Child 只能调用绑定的 `readNovelSourceSlice`，最终 JSON 经 Host 校验并写入 Child-owned attempt Artifact。Recipe 单元显式投影回 Planner 已批准步骤；Run-tree journal 可持久化 PurrA 冻结 JSON 序列 | Root/Child 归属、一次受限读取、最终结果和 Artifact 创建者由集成测试共同验证；scalable/v1/composition/run-tree 联合回归 113 项通过 | Reduce/Synthesize/Coverage/Review 仍是显式测试 fixture；未执行真实 Provider、Electron、取消或重启恢复；生产 registry/route 未改 |
| 2026-09-15 | Hierarchical Reduce 输入编译第一批：Host 严格按 durable Unit dependencies 解析已完成 attempt Artifact，校验同一 pass、fan-in、资源引用、payload digest 和无重叠 slice lineage；Child scope 只保存 Artifact locator/digest/lineage，不放 findings 正文。序列化输入超过所选窗口 40% 硬上限的 90% 安全装箱预算时失败关闭 | 正常 locator、缺失依赖、错误 pass、重叠 lineage 和超预算均有确定性测试；Map 首轮请求无正文的集成断言通过 | 尚未实现 Reduce 受限读取 Tool、Child model、归并输出 Artifact 与递归层集成 |
| 2026-09-15 | Hierarchical Reduce 执行纵切：新增无模型参数 `readNovelAnalysisReduceInputs()`，按当前 Child Run 的冻结 locator 加载并复核 Artifact digest/lineage；Reduce Child grant 只允许该工具且禁止 spawn。Host 校验 findings/conflicts 维度，提交 Child-owned attempt Artifact，持久 `inputArtifactIds + coveredSliceIds`，同 attempt 重放不再调用模型 | Tool 越界面为空、public Run-tree command、无 findings 的 Child input、Child ownership、结构化输出、完整 lineage、模型错误分类和 Artifact 重放均有测试；scalable/v1/composition/run-tree 联合回归 120 项通过 | 尚未在真实 durable DAG 中用模拟 Provider 跑两层以上 Reduce；Synthesize/Coverage/Review 未实现；生产未切流 |
| 2026-09-15 | 多层 Reduce 模拟 Provider 集成：同一真实 AgentCore/durable recipe 对 4+ Slice 执行 Map，再执行 fan-in=2 的至少两层 Reduce；每个 Child 实际调用自身唯一获授权 Tool，每层只读取直接依赖，最终 Reduce Artifact 的 `coveredSliceIds` 覆盖全部 Map slice | 逐次工具读取、直接依赖 fan-in、Root/Child 归属、Child-owned Artifact 和最终 lineage 在同一集成测试中验证；相关联合回归 120 项通过 | 为隔离 DAG 正确性，本门禁将 recipe 并行度固定为 1；使用默认并行度时测试未在合理时间内终止，增加 tree slot 未解决，不能归因为单纯容量不足。需先诊断并行 spawn/join 调度再实现 Synthesize；生产未切流 |
| 2026-09-15 | 并行 Child join 收敛：确认阻塞来自多个 durable Unit 对同一 requester Root 并发执行 `join_agent_runs`，不是模型耗时、正文大小或简单 tree slot 不足。新增 per-Root join coordinator：各 Unit 保留独立幂等 spawn，同一调度拍的 Child 合并为一次 join，后一批等待前一批完成，避免并发切换 Root waiting 状态 | 4 个并发 join 被合并为一次的确定性测试通过；多层模拟 Provider 测试恢复默认 maxParallelism=4 后约 2 秒完成，Map/Reduce 工具、Artifact 与最终 lineage 断言全部通过；联合回归 121 项通过 | 尚未覆盖中途取消一个 batch、进程在 spawn 后/join 前重启；这些进入恢复门禁。Synthesize/Coverage/Review 与生产切流仍未实现 |
| 2026-09-15 | Synthesize 整书总结纵切：Host 只接受每个 Planner pass 的最终根 Artifact，逐 pass 校验完整 Slice lineage、依赖顺序、digest 和 40%×90% 输入预算。Synthesis Child 通过无参数 `readNovelAnalysisSynthesisInputs()` 读取绑定结果，不能读原文或选择输入；输出非空 `summaryMarkdown` 与 Planner 指定 sections，并提交 Child-owned attempt Artifact | 默认并行 Map、两层 Reduce、Synthesis 在同一模拟 Provider/真实 AgentCore DAG 中完成；验证 Synthesis Tool 恰好一次、总结正文、sections 和全部 Slice lineage；相关联合回归 121 项通过 | `summaryMarkdown` 尚未越过 Coverage/Review 投影为 Root finalResponse；取消、重启恢复、真实 Provider、Electron 与生产切流未覆盖 |
| 2026-09-15 | 确定性 Coverage Gate：不调用模型，Host 从 Synthesis 反查每个 pass 的最终根 Artifact，重新校验期望 Slice/pass、完整 lineage、唯一输入、非空总结和 immutable payload digest；通过后写 Root-owned coverage attempt Artifact，记录 synthesis digest 与各 pass root receipt | 正常门禁、同 attempt 重放、缺 Slice、空总结均有定向测试；并行 Map→两层 Reduce→Synthesis→Coverage 在同一真实 AgentCore 模拟 Provider DAG 中完成；联合回归 123 项通过 | Review/发布和 Root finalResponse 尚未实现；取消、重启恢复、真实 Provider、Electron 与生产切流未覆盖 |
| 2026-09-15 | Review 与 Root 最终回复纵切：Review Child 只获得无参数 `readNovelAnalysisReviewInput()`，只能读取 Coverage 已批准并绑定 digest 的 Synthesis，不可读取原文或选择其他 Artifact；输出保持 section 标题及顺序，提交 Child-owned review Artifact，并把审核后的 `summaryMarkdown` 投影为 Root `finalResponse` | 默认并行 Map→多层 Reduce→Synthesis→Coverage→Review 在同一真实 AgentCore 模拟 Provider DAG 中完成；断言 Review Tool 恰好一次、Artifact lineage 正确、最终用户回复为自然语言正文而非 URI/状态文案；相关联合回归 125 项通过 | 取消、spawn/join 间重启恢复、真实 Provider、Electron 与生产切流仍未覆盖；下一门禁只处理恢复语义，不扩展分析功能 |
| 2026-09-15 | 已落库结果的重启恢复：统一 attempt Artifact 查询只在 `execution_interrupted` / `execution_recovery_after_restart` 时回看上一 attempt；Map、Reduce、Synthesize、Coverage、Review 均把有效旧结果收敛到当前 attempt。Child-owned 结果允许从旧 Root 迁移到 continuation Root，但仍校验其确为原 Root 的直接 Child；普通模型错误明确禁止复用 | Map 与 Review 覆盖跨 Root continuation，断言模型调用次数不增加；取消一个 join batch 后同 Root 后续 batch 仍可执行；含既有 replacement restart、run-tree journal 的联合回归 133 项通过 | 仍需验证“已 spawn 但尚未形成 finalized Artifact”的 Child 在进程重启后的明确处置；真实 Provider、Electron 与生产切流未覆盖 |
| 2026-09-15 | 未完成 Child 的重启/取消收敛：通用 orphan settlement 与无 live executor 的显式取消都会先对持久 Run-tree 执行 `cancel_subtree(rootRunId)`，再结算 SQL Root；不存在对应 Tree Root 的普通 Run 维持原行为，journal 错误继续失败关闭 | 新鲜 repository 重放确认 Root 与所有 unfinished Child 均为 canceled，aggregation 不再返回 pending；显式取消同样覆盖 subtree；包含 startup/recovery/scalable 的联合回归 163 项通过 | 确定性恢复门禁已闭合；下一项使用隔离数据库和真实 Provider 验证工具自主选择、长上下文预算、最终总结及 Provider lease，之后才能讨论生产切流 |
| 2026-09-15 | 隔离真实 Provider 纵切：Planner 在无原文上下文中生成语义计划；Host 将来源编译为 2 个 slice，依次完成 2 Map、Reduce、Synthesize、Coverage、Review。所有 Child 均通过唯一获授权的 READ Tool 获取绑定输入，Root 返回自然语言整书总结 | 隔离 Run `run_98720f0341454884` 的 6 个 Unit 均首次 completed；持久事件记录 2 次 `readNovelSourceSlice` 以及 Reduce/Synthesis/Review 各一次绑定读取；Provider lease 归零；本轮相关回归 93 项通过 | 这是 32K 窗口、小型合成来源和命令行入口的真实 Provider 证据；尚未完成 256K/1M、500 万字合成 fixture、生产 Profile 切流和 Electron 验收 |
| 2026-09-15 | scalable Review 产品只读投影：识别 v2 整书 `summaryMarkdown + sections`，页面按模型规划的分析栏目展示，不把通用栏目伪装成旧 facts/craftCards；在建立明确的可编辑资料映射前禁止发布 | 新旧 Review Artifact 投影测试通过；TypeScript 类型检查和来源分析页面定向行为测试通过 | 生产新建入口仍未切到 scalable Profile；v2 结果目前只读，后续需单独决定哪些结构可进入正式来源资料 |
| 2026-09-15 | scalable v2 持久实现身份：v2 使用独立 `implementationVersion=2/toolContractVersion=2`，不用 recipeVersion 冒充实现版本；注册表可以对持久 Run 做精确版本路由 | v1/v2 身份隔离和精确路由测试通过；composition/profile/routing 联合回归 109 项通过 | 根据“不处理旧数据”的产品决策，后续生产 composition 只安装 v2；v1 Profile 仅留在冻结代码与隔离测试中，不是运行时回落路径 |
| 2026-09-15 | 生产新建入口切换到 scalable v2：production composition 只安装 v2 小说分析 Profile；create identity 固定为 implementation v2/recipe v2；入口只传 revision/command，SliceManifest 由 Host 编译，Durable executor 改为 scalable Map/Reduce/Synthesize/Coverage/Review。新 v2 追问继续通过受限 Tool 读取来源或绑定 Review Artifact | scalable 纵切、生产 composition、create route、入口 executor、新 v2 Artifact 追问及共享 composition 联合回归 104 项通过 | 按产品决策不迁移、不恢复、不继续执行旧 v1 数据；尚需重启实际应用并以全新 Run 做 Electron 验收 |
| 2026-09-15 | scalable v2 正式资料合同：Synthesize 不再生成 `analysisSections` 报告，而是直接生成现有续写链路消费的 `facts + craftCards + storyOverview/summaryMarkdown + analysisTechniqueResult`；Review 逐项审核相同对象并保持 ID，Review 投影可直接进入既有人工编辑、发布和续写初始化流程 | Canonical validator 拒绝非现行 factKind、重复 ID、悬空技法引用和报告 sections；集成测试覆盖 Review Artifact → 用户审核 Artifact → 正式分析表 → `ContinuationService.preview_canon`，确认人物资料沿用既有 `character` material mapping | 不兼容、不恢复此前只读 `summaryMarkdown + sections` Artifact；必须新建分析 Run 生成规范资料 |
