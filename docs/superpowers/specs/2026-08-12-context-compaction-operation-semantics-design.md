# 上下文压缩 Operation 语义修复设计

## 问题

`ContextCompressionCoordinator.prepare()` 会在每次上下文预算检查时创建
`context_compaction` Operation，即使当前请求远低于压缩阈值。一次 Agent Run
会在规划前、规划后以及每个模型轮次调用该协调器，因此普通短对话会在执行面板中
出现多次“压缩上下文”。持久化记录证明这些 Operation 对应的结果均为
`below_threshold` 或 `within_budget`，没有发生语义摘要或消息裁剪。

## 根因

协调器已经在创建 Operation 前计算出 `compression_required`，但 Operation 生命周期
目前无条件启动。前端正确地把规范 Operation kind `context_compaction` 显示为
“压缩上下文”；错误发生在 Core 生产了语义不成立的 Operation，而不是前端渲染。

移除虚假 Operation 后还暴露出一个被延迟掩盖的委派竞态：子 Run 创建事务已经原子
绑定 delegation，`AgentDelegationCoordinator` 随后会用同一 child Run 再确认一次
绑定。SQLite adapter 原本把这次幂等确认当成失败，而且它的 `UPDATE` 与
`SELECT changes()` 不在同一事务中；虚假压缩 Operation 产生的并发写入曾偶然把零行
更新误判为成功。没有该延迟时，委派会稳定报 `delegation_submit_failed`。

## 修复

只有 `compression_required` 为真时才启动 `context_compaction` Operation。低于阈值时
仍调用应用压缩 Hook，以便复用已持久化摘要、生成请求投影和保留诊断 Trace，但不向
用户操作时间线声明执行了压缩。

真正需要压缩时保持现有行为：Operation 在压缩 Hook 或默认裁剪前开始，并以
`succeeded`、`failed` 或 `canceled` 之一终结。规划前、规划后和模型轮次仍各自执行
预算检查；本修复不合并这些检查，也不改变压缩阈值、摘要策略或消息内容。

委派绑定保持由子 Run 创建事务原子落库。同一 delegation、同一 child Run、同一
worker 的后续确认属于幂等成功，不重复写 running 事件；不同 child、worker 或谱系
不匹配仍失败。绑定的读取、校验、条件更新、变更数判断和事件写入必须处于同一数据库
事务，不能再依赖连接级 `changes()` 的跨任务时序。

## 不采用的方案

- 不在前端隐藏或按时长过滤 Operation。Operation started 事件没有压缩结果，前端
  无法可靠区分预算检查与实际压缩，这也会保留错误的持久化语义。
- 不按 Run 合并所有压缩 Operation。长工具循环可能在不同模型轮次合法地再次达到
  阈值，合并会吞掉真实生命周期。

## 验证

- 低于阈值且安装了 Operation controller 时，结果保持 `below_threshold`，应用 Hook
  仍被调用，但 Operation 输出为空。
- 超过阈值时仍产生且只产生一对同 ID 的 `context_compaction` started/finished 事件。
- 子 Run 已原子绑定后，相同 attach 确认返回成功；父/子 Agent SSE 生命周期保持
  `claimed → running → done`。
- 运行上下文压缩定向测试和 Agent Core 架构门禁，确认没有改变压缩结果、预算安全或
  Operation 终态约束。
