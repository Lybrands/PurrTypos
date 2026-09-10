# 剧本 Agent 有界执行重写：Phase 0 基线

> 日期：2026-08-25
>
> 状态：Phase 0 完成，Phase 1 尚未开始
>
> 约束：本阶段只增加事故 fixture、回归测试和测量文档，不改变生产行为，不重启后端

## 1. 结论

Phase 0 证实了原计划的核心判断：`series_arc` 仍是一个会同时吸收全剧阶段和逐集展开的
超大 Run；增加 Provider deadline 只能推迟失败，不能建立业务边界。

同时发现两个必须在 Phase 1 先解决的前置问题：

1. PurrA 已有 `LongTaskBudgetLimits` 和幂等 `record_usage`，但当前剧本执行器没有把 Part
   Run 的 usage 汇入 Long Task。历史剧本 Long Task 的 usage 因而全部为零；只配置 limits
   不会得到有效的任务总预算。
2. 新执行图中的 chapter digest、reduction、series phase、episode fragment、scene draft 等
   Part 尚不存在。Phase 0 不能从不存在的 Run “实测确定”最终 cap，只能记录旧实现基线和
   provisional cap；最终值必须随对应 Phase 的实现和真实 Provider 验证校准。

这两个结论修正的是实施顺序，不改变所有权：PurrA 继续拥有通用预算协议与原子记账，
PurrTypos 负责业务 Task Contract、Part Contract 和调用已有记账接口。

## 2. 工作区与运行态快照

| 检查项 | 事实 |
| --- | --- |
| 分支 / HEAD | `feat/0.6` / `90e5c46` |
| PurrA 实际导入 | `/Users/liuyubin/Lybrand_project/purra/src/purra/__init__.py`，editable distribution `0.4.0` |
| 声明依赖 | `backend/requirements-purra.txt` 仍为 `purra==0.3.0`；本机 `.venv` 当前使用兄弟目录的本地 editable source |
| 后端 | PID `46860`，命令为 `.venv/bin/python -u backend/main.py`，没有 reload 参数 |
| 进程操作 | 本阶段未重启、未停止后端 |
| 剧本 Operation | 7 条：2 succeeded、1 canceled、4 failed |
| 剧本 Long Task | 6 条：2 completed、1 canceled、3 failed，全部 recipe v5 |
| 活跃 v5 Task | 0；没有 pending/running/paused/queued/waiting_retry |

进程和数据库状态是本次测量时点的快照，不应在后续 Phase 被当成永久事实；进入下一阶段前
仍需重新做只读检查。

本地 `0.4.0` 高于声明的发布版本不代表产品依赖已经升级。宿主门禁只对精确的兄弟目录
editable distribution 允许开发覆盖；CI 和非 editable 环境仍严格要求 `purra==0.3.0`。
Phase 1 所需的 `LongTaskBudgetLimits`、`LongTaskUsage` 和 `record_usage` 已在 PurrA `v0.3.0`
tag 中核实存在，因此本轮应用改造不依赖未发布的 0.4.0 API。

## 3. 事故回放

事故 `run_609d1ffa3eb64cfd` 已固化为不含 prompt、正文和私有 reasoning 的 fixture：

- Unit：`section:structure:series_arc`；
- 终止码：`model_invocation_deadline_exceeded`；
- 初始 Unit：8；当时只完成 `document:evidence`，episode index 尚未开始；
- 模型尝试 4 次，其中 3 次有 Provider usage，1 次 usage 未报告；
- 已报告 input 92,285、output 878、reasoning 443 tokens；
- Provider 输出事件 469 条、持久化 payload 1,628,472 bytes；
- 失败 invocation 含 442 条 `provider.delta_batch`，序列化 payload 共 1,513,586 chars；
- 持久化 Provider deadline 为 120,000 ms，Run 约 129,000 ms 后失败；
- delegation 为 0。

这里的 1,513,586 chars 是事件 payload 的序列化体积，不等同于 151 万字模型正文，也不能
换算成 token。它证明流事件/信封存在显著放大，但根因判定仍以 Unit 边界、Provider usage、
deadline 和完成进度为准。

新增越界样本固定了 Phase 3 的预期：`series_arc.phases[*]` 一旦递归出现 `episodes`，业务
validator 必须以 `series_arc_contains_episode_expansion` 拒绝，不能让主线 Part 顺带生成
逐集结构。

## 4. 当前正确拆分

以下行为已经由现有 manifest 测试冻结，后续重写不得退化：

| Deliverable | 当前正确边界 | Unit 总量公式 |
| --- | --- | --- |
| `sceneList` | 每集一个 AI Part | `E + 3` |
| `screenplayDraft` | 每场一个 AI Part；每集另有 metadata 与 validation | `S + 3E + 1` |
| `review` | 每集 5 个维度 Part，同集可并行，随后逐集 validation | `7E + 1` |

其中 `E` 为纳入本次正式任务的集数，`S` 为场景总数。当前产品已有 `E <= 100`，但没有找到
正式、统一的“每集最大场景数”准入，因此 Draft 的 Task Unit 上界仍未闭合。这不是靠 token
cap 能替代的变量，必须在 Phase 4 前作为产品 scope 约束确定。

目标执行图的 Unit 公式如下：

| Deliverable | 目标 Unit 总量公式 | 当前可证明上界 |
| --- | --- | --- |
| `sourceAnalysis` | `N + R(N) + 8` | 未闭合；授权叶子章节数 `N` 尚无正式任务上限 |
| `creativeBrief` | `8` | 8 |
| `structure` | `P + E + C + 7` | 131，因 `P <= 12`、`E <= 100`、`C <= 12` |
| `sceneList` | `E + 3` | 103 |
| `screenplayDraft` | `S + 3E + 1` | 未闭合；缺少场景 scope 上限 |
| `review` | `7E + 1` | 701 |

`R(N)` 是 fan-in 12 的归并节点数：`N <= 12` 时为 0，否则为
`ceil(N / 12) + R(ceil(N / 12))`。这些公式是 Compiler 准入条件，不是 UI 公开 Plan 步骤数。

## 5. Usage 与输出样本

### 5.1 Long Task 记账缺口

6 条历史剧本 Long Task 的 `usage_json` 全部为零，budget limits 为空或各字段为 `null`。
代码链也与数据库事实一致：

- PurrA port 已声明 `LongTaskRepository.record_usage(...)`；
- SQLite 实现按 `(task_id, run_id)` 幂等写入并聚合 usage；
- `screenplay_agent_task_executor._unit_result()` 只返回 Run/Artifact/receipt 元数据；
- 当前剧本执行链没有调用 Long Task `record_usage`。

`screenplay_agent_operation_usage` 只能看到 Root/continuation Root Run 的少量 usage，不包含
Durable Part Run，不能拿来代替 Task 总量。Phase 1 必须先接通 Part Run settlement，并确保
成功、失败、重试和 usage 未报告场景都 fail closed 且不重复记账。

### 5.2 可观测旧样本

旧完成 Candidate 的 `items_json` 只能给出 envelope-inclusive 字符数，不能冒充 token：

| Deliverable | Part 字符数 | min / median / max |
| --- | --- | --- |
| `sourceAnalysis` | world 5,206；characters 6,382；story 8,392；themes 5,327；adaptation risks 5,911 | 5,206 / 5,911 / 8,392 |
| `creativeBrief` | premise 1,001；positioning 1,974；adaptation 5,718；world 7,784；characters 9,584 | 1,001 / 5,718 / 9,584 |

这些旧 Part 的持久化模型计数为零，不能用于 reasoning/output token 分位数。

一条完成的旧 `series_arc` Run 可作为“不要继续维持大单元”的反例：6 次模型尝试，input
268,077、output 27,228、reasoning 18,460；单次最高 output 13,349、reasoning 13,189；
Candidate envelope 11,837 chars，耗时约 221 秒。它证明旧单元偶尔可以完成，但成本和尾延迟
过高，不证明 120 秒或 300 秒 deadline 能让该设计稳定。

### 5.3 Provisional Part cap

下表是进入实现阶段的初始候选值，不是已验证的最终值，也尚未写入生产配置：

| Contract 类别 | provisional `output_token_cap` | 依据 |
| --- | ---: | --- |
| `final_response` | 1,024 | 只输出 2 至 5 句公开总结，无工具和正文 |
| episode metadata；series/episode/character index | 4,096 | 有界身份与短摘要，禁止正文展开 |
| chapter digest、reduction、creative brief section、review dimension | 8,192 | 紧凑 Schema；旧 Candidate 最大约 9.6k chars |
| source analysis synthesis、series phase、episode fragment、character arc、scene-list episode、draft scene | 16,384 | 正文或 reasoning 较重；覆盖旧 series-arc 单次 13,349 output 的保守起点 |
| 剧本工作流绝对上限 | 32,768 | 只作最后安全边界，不允许业务 Part 直接继承 |

Phase 1 可先把 contract 映射到这些 provisional 值，但每个后续 Phase 都必须用新 Schema 的
scripted boundary test 校准；Phase 7 还要用真实 Provider 验证正常样本不会被错误截断。

### 5.4 Provisional Task budget 公式

对一个 manifest，先计算 AI Part 集合 `A`、reasoning-enabled 子集 `H`，并为每个 Part 定义
provisional invocation attempt 额度 `M(p) = 8`：

```text
max invocation attempts = sum(M(p) for p in A)
max input tokens        = 300,000 * |H| + 100,000 * (|A| - |H|)
max output tokens       = sum(p.output_token_cap * M(p) for p in A)
max reasoning tokens    = sum(p.output_token_cap * M(p) for p in H)
```

300,000 input/重 reasoning Part 只由旧 `series_arc` 的 268,077 样本支撑，是保守首值；
100,000 input/其他 Part 和每 Part 8 attempts 是安全策略，不是统计分位数。Phase 1 scripted
tests 必须证明预算在新增调用前生效，Phase 7 再根据真实 Provider usage 向下收紧。任何
`unreported_usage_attempts > 0` 且启用了 token budget 的 Part 都应沿用 PurrA 的
`provider_usage_unreported` fail-closed 行为。

这里必须乘以 `M(p)`：Part cap 限制的是单次 invocation，一个正常 Part Run 可能包含读取
工具、写 Candidate 和最终收束等多轮模型调用。只把 cap 按 Part 相加一次会系统性低估任务
总 output/reasoning 预算。

## 6. Phase 0 变更与验证

新增或修改：

- `backend/tests/fixtures/purra_incidents/2026-08-25-screenplay-series-arc-deadline.json`；
- `backend/tests/test_purra_incident_replay.py`；
- `backend/tests/test_purra_package.py`，保留发布 pin 严格校验，同时允许精确兄弟目录 editable
  联调；
- `docs/design/2026-08-25-screenplay-agent-bounded-execution-rewrite-plan.md`；
- 本基线文档。

本阶段没有修改 PurrA、应用生产代码、数据库、前端或 Provider 配置。

验证结果：

| 检查 | 结果 |
| --- | --- |
| 事故、执行图、Durable Service、工具目录与规划约束 | 274 passed |
| `test:screenplay-acceptance` | 217 passed |
| `check:agent-refactor-boundaries` | 53 passed |
| `npm run check` | passed：typecheck、395 Node tests、1,559 backend tests |
| `git diff --check` | passed |
| 进程复核 | 原后端 PID 46860 和前端 PID 46861 仍在；测试未留下 4173 监听进程 |

边界门禁只出现 npm 对旧 mirror 配置的弃用 warning，没有测试失败；该 warning 与本次剧本
重写无关，本阶段不扩张到 npm 配置清理。

Phase 1 的入口条件：

1. 事故回放和现有正确拆分测试通过；
2. PurrA 本地导入路径仍成立；
3. 没有活跃 recipe v5 Task；
4. Phase 1 先实现 usage settlement，再启用非空 Long Task budgets。
