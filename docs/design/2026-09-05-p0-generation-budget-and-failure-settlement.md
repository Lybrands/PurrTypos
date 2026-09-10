# P0：生成预算与错误终态

状态：本地破坏性修复与确定性回归完成；未发布，真实 Provider 验收尚未执行。

## 事故与根因

持久化事故分析以 `run_d965e1aa26d44632` 为主要样本，
并关联先前的 `run_daefdbe0dd454b4c`。主要样本中，Root 使用的模型能力上限
为 393216 tokens，而剧本子调用受到 16384 tokens 的业务 Part 限制。
该调用报告 16384 reasoning tokens、零正文，最终以 `length` 结束。
这不能解释为整个任务累计预算已经用尽，也不能由降低用户思考强度解决。

故障包含相互独立的控制边界：

1. 业务结果容量被当成 Provider 总生成额度，思考和结果共用的额度提前耗尽。
2. 技术错误被映射成可恢复暂停，或在已完成单元的 observer 回调失败后被错误地
   当作单元执行失败处理，原始错误丢失并留下运行中状态。
3. checkpoint 规划或应用异常没有完整收敛；多集 review 门禁只统计已经过的
   validation 时，可能提前等待尚不具备生成条件的聚合 checkpoint。
4. 宿主新建私有模型任务 runner 时采用调用者自身作为 Root 身份，未核验已经
   持久化的模型和用户生成/思考配置。

## 当前不变量

- 模型能力上限、用户显式生成上限、工作流结果容量目标分别建模和记录。
- 工作流容量目标不缩小 Provider 总生成额度。有效额度由模型能力、用户上限及
  实际可用上下文约束；上下文约束来源单独记录，不冒充用户配置。
- Provider 必须回报真正应用的生成限制。流式与非流式均在提交成功前验证限制、
  用量和结束原因；`length` 结果不进入 Schema 修复重放或工具执行。
- 已发生的调用及用量仍被记录。未知 reasoning 用量保持未知。
- 思考模式、强度、显式思考预算不由业务 Part 改写。受管 Hook 在调用前与已保存
  Root 身份摘要核验；仅允许任务输出格式、宿主进展能力声明、凭证轮换和规范化
  等价地址等明确差异。
- 技术错误最终以失败结束；用户取消、显式暂停、业务范围待确认各自保持语义。
- checkpoint 的 planning/ready/applying 异常按身份、摘要和租约 owner/epoch
  收敛为失败。旧执行者不能覆盖新租约。若数据库本身不可写，无法保证落盘终态，
  但原异常和收敛异常链必须传播。
- 通用持久层只拥有事务内的可注入准入回调。剧本 checkpoint 查询和门禁由产品
  adapter 实现，在 claim 计数和租约变更之前执行。
- 多集 review 允许后续分集完成，仅在全部 review validation 完成且候选位于
  最后一个 validation 之后，才要求聚合 checkpoint 已应用。

## 主要回归入口

- `test_purra_output_budget.py`、`test_purra_runtime.py`、`test_native_provider_gateways.py`：
  生成额度来源、配置传递、Provider 限制回执和截断终止。
- `test_agent_composition.py`：持久化 Root 身份、配置漂移零调用拒绝、正常私有任务。
- `test_screenplay_agent_durable_service.py` 中的
  `test_checkpoint_planner_failure_terminates_every_owner_without_replay`：
  reserve 后截断/异常，Checkpoint、Root、LongTask、Operation、Turn 均失败，
  无产物提交、无重复模型调用、重复执行不重放，并有超时约束。
- `test_screenplay_agent_rewrite.py`：checkpoint 各阶段异常、过期 owner 隔离、
  多集 review 门禁，以及合法业务范围暂停/恢复。
- `test_long_task_claim_guard.py`：准入允许、等待、异常均保持 claim 事务原子性。
- `test_purra_package.py` 与包逐字节检查：源码、wheel、实际安装包一致，旧字段和
  已删除模块不残留于分发包。

## 本地最终验证（2026-09-05）

| 检查 | 结果 |
| --- | --- |
| PurrTypos 全量后端，实际安装 wheel、无源码路径覆盖 | 2055 passed，0 failed，0 skipped |
| PurrTypos 前端/Node 单元测试 | 434 passed |
| Purr Components、TypeScript 类型与架构边界 | 通过 |
| PurrA Python Core 全量 | 486 passed |
| PurrA TypeScript Core | 267 passed，类型与示例检查通过 |
| `npm run build:web` | 前端及后端资源打包通过 |
| 三个 Core/Provider 包 | PurrA 源码、vendor wheel、已安装文件逐字节一致 |
| 分发后端 | 389 个源文件与 build-resources/backend、dist-web/backend 一致 |
| 分发包导入/资源 smoke | 3 个 PurrA 包及 main 从分发目录导入，22 个步骤资源可加载，0 Provider 调用 |
| 两仓库 `git diff --check` | 通过 |

后端 JUnit 输出在 `/private/tmp/purrtypos-p0-final-backend.xml`；PurrA Python
JUnit 输出在 `/private/tmp/purra-p0-final-python.xml`。它们是本地临时验证产物，
不是提交内容。前端构建保留大 chunk 提示；本轮没有做 bundle 拆分或声明该告警已解决。

所有本轮测试/构建进程已结束，未留下开发服务器。

## 发布与验收边界

本地修改不做旧字段别名、旧 Run 续跑迁移，不删除历史数据。需要新建对话使用
新契约。包版本尚未提升，也未发布框架、推送分支或创建 Release。

确定性测试能证明本次已识别的预算混用、错误隐藏和配置越权路径已被关闭；不能
保证任意模型永不触及上下文或生成上限，也不能证明真实模型延迟或长任务成功率。
该验证未调用真实模型。真实 Provider 长剧本、用户指定思考强度
和较小显式生成上限下的行为仍是上线验收项。

放开错误的 Part 上限后，真实调用可能使用更多 tokens、时间和费用。这是保留
用户配置的成本，不应被描述为已经获得性能提升；速度需要单独依据调用链耗时测量。
