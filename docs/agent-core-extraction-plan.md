# Agent 三层架构改造完成记录

> 状态：代码改造完成。`/ai/chat/stream` 只运行新的三层 Agent 架构；仓库不再提供旧 Agent loop、Runtime 兼容桥或新旧切换开关。

## 1. 最终结构

### Agent Core 层

`backend/agent_core/` 是业务无关的执行内核，负责：

- 规划、Run 状态机和终态一致性；
- 上下文预算、模型轮次和流式中断处理；
- 工具 Schema、授权范围、批次校验和执行限制；
- 人工审批、取消、Trace 和确定性评测；
- 响应约束及语义 Judge 的通用端口。

Core 不导入 HTTP DTO、Writing 数据库实现或写作业务规则。

### Writing 业务层

`backend/domains/writing/` 负责：

- Writing 上下文、Planning Policy 和工具风险政策；
- 章节、大纲、人物、设定、记忆等工具业务契约；
- 写作响应格式、证据约束、摘要与连续性判定；
- Writing 专属回归案例和安全红队案例。

业务层通过 Core 契约表达能力，不负责 FastAPI、SSE 或供应商 SDK 生命周期。

### 应用与基础设施层

主要入口为：

- `backend/application/agent_composition.py`：唯一 Composition Root；
- `backend/application/request_mapping.py`：HTTP DTO 到 Core 请求的映射；
- `backend/application/sse_mapping.py`：Core 更新到现有 SSE 协议的映射；
- `backend/application/response_judging.py`：模型语义 Judge 适配；
- `backend/infrastructure/models/`：模型供应商适配；
- `backend/infrastructure/persistence/`：Run 与 Writing 持久化；
- `backend/infrastructure/writing/`：技能声明和 Writing 工具实现装配；
- `backend/routers/ai.py`：HTTP/SSE 边界及断连传播。

## 2. 唯一运行路径

`POST /api/ai/chat/stream` 的固定流程为：

1. 校验 API Key 与模型参数；
2. 将 `ChatStreamRequest` 映射为 `AgentRunRequest` 和 `WritingDomainContext`；
3. 由 `AgentComposition` 创建完整 `AgentCore`；
4. Core 完成规划、上下文构建、模型调用、工具执行与终态持久化；
5. 应用层把 Core 更新映射为 SSE；
6. 断连信号贯穿模型、工具和审批，并最终持久化取消状态。

不存在按环境变量选择旧实现的分支，也不存在失败后退回旧 loop 的行为。

调用方自定义的非空 `tools` 或显式 `tool_choice` 不属于当前产品契约。此类请求会在映射边界失败关闭，不调用模型，也不执行任何工具。

## 3. 已删除的迁移设施

改造收口时删除了：

- 两个 Agent 路径切换环境变量；
- Router 内联旧模型/工具 loop；
- Core Runtime 到旧 SSE/工具栈的兼容桥；
- 旧 Planner、Run Controller 和工具执行兼容栈；
- 新旧路径双轨 Pilot、发布比较、人工评分及其脚本；
- 仅用于保留历史 Python import 的 facade。

这些设施用于迁移期对比和回滚，不是最终产品架构的一部分。

## 4. 长期不变量

1. 模型文本和检索内容都不是授权来源。
2. 当前步骤的工具授权由宿主决定，未知或越权工具整批拒绝。
3. 高风险持久化操作必须等待一次性的当前用户审批。
4. 审批拒绝、取消、业务阻塞和系统失败保持不同终态。
5. 工具参数、调用 ID、批次大小和结果体均受确定性校验。
6. Writing 对象必须校验当前书籍归属。
7. 流中断不得重复执行已经完成的工具副作用。
8. 不合规候选回答在修复或判定完成前不得展示。
9. 每个模型调用尝试必须留下可计量的终态 Trace。
10. HTTP/SSE 层不重新实现 Core 或 Writing 规则。

## 5. 验证边界

代码验收以确定性测试为准，重点包括：

- Agent Core contracts、planner、runtime、tool executor 和 cancellation；
- Writing planning、context、tool scope、response validation 和 judging；
- Composition Root、请求映射、SSE 映射及真实 HTTP 审批契约；
- 主应用 lifespan、断连清理和持久化。

真实模型的随机输出或外部网络波动不再触发自动的新一轮架构改造。发现问题时，先判断它是否违反上述宿主不变量；只有代码缺陷才进入修复和确定性回归。

## 6. 历史结论

迁移期间的双轨运行曾发现并修复格式判定、流中断重试、性能计量和资源关闭问题。这些修复已经进入唯一的新架构；历史 Pilot 批次、冻结源码、报告和评分数据均已删除。
