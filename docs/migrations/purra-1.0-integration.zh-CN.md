# PurrTypos 的模型委派与普通操作

PurrTypos 使用 `backend/purra-candidate.json` 和 `backend/requirements-purra.txt`
中的精确本地 wheel 哈希。候选版本仍为 1.0.0；不能仅凭版本号确认代码。

## 唯一执行路径

普通模型／工具 Unit 调用 `run_durable_operation`，通过 PurrA 的
`AgentCore.execute_operation` 在所属 Run 内执行。Unit 的租约、任务绑定与
所属 Agent 的能力上限仍会校验。操作不创建 Agent，不提交主 Run 的最终状态，
也不覆盖主 Run 的恢复检查点。调用预算与工具事件归属主 Run。

真正的 Agent 委派由模型调用 `delegateToAgents` 决定。职责指导位于宿主
`agent_delegation_policy.py`，没有内置业务角色或 Unit 到 Agent 的映射。
模型使用 `listAgents`、`getAgent` 查看职责，用 `continueAgent` 保留同一身份
和上下文继续交谈。最多 16 个参与 Agent（含主 Agent），单次创建和运行并发
上限均为 3；普通 Unit 的并发由长任务调度器独立限制。

宿主显式配置逐条反馈说明；子 Agent 的结果回到主 Agent 后统一排队输出。
普通模型操作的结果经过校验、保存后，由主 Run 额外生成一段公开阶段说明。
阶段说明逐条排队，其他操作可以继续执行；最终回复等待这些说明完成。
内部模型正文仍保持私有，阶段说明只接收宿主筛选的已验证结果。
每项说明增加一次无工具模型调用，计入主 Run 预算。交付记录持久化：完成后去重，
中断后要求对账，不能为了重试说明而重新执行已完成的业务操作。
真实子 Agent 内部的操作不直接公开，由主 Agent 的协作者结果接收路径统一汇报。

## 产物与证据

小说分析与技巧生成按操作身份保存提交结果。剧本候选按所属 Run、任务和 Unit
定位；一个 Run 可以拥有多个候选。剧本依赖读取凭据按 `executionScopeId`
隔离，不能使用并行操作的工具结果满足自己的读取要求。候选在操作完成后校验、
定稿，主 Run 保持运行。任务用量在主 Run 上累计记录。

## 存储与验收边界

新的 Agent 命令日志使用 `ai_agent_tree_commands_v3`，并绑定当前参考状态机摘要。
旧表保留供诊断，不删除、不按新规则重放，不为旧任务自动续跑。
新增分页查询端口由 SQLite 适配器直接提供。

本轮使用合成材料和临时数据库进行确定性验证。真实 Provider、Web/Electron
交互和历史任务恢复需单独验收；既有窗口模式记录不代表本候选通过这些验收。
