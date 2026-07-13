# PurrTypos Agent 最终架构

## 1. 架构目标

Agent 的核心不是“让模型可以调用工具”，而是让不确定的模型输出进入一个可约束、可观察、可恢复、可评测的宿主执行系统。

## 2. 分层结构

| 层 | 职责 | 主要模块 | 不应承担的职责 |
|---|---|---|---|
| API / Stream | 接收请求、流式事件、连接中断 | `routers/ai.py` | 决定工具安全策略 |
| Context | 动态分配上下文预算、召回记忆、裁剪完整对话轮次 | `utils/context_budget.py`、`memory_orchestrator.py`、`chat_preflight.py` | 执行写工具 |
| Planner | 将用户目标规范化为有序步骤及建议工具 | `task_planner.py` | 直接执行工具或静默兜底 |
| Run Controller | 管理 Run、To-do、当前步骤和状态转换 | `agent_run_controller.py`、`agent_run_store.py` | 判断模型内容质量 |
| Tool Contract | 保证模型可见 Schema 与后端 handler 一致 | `tool_contract.py`、`tool_router.py` | 代替运行时权限校验 |
| Tool Policy | 定义 read/propose/confirm 风险和审批要求 | `tool_policy.py` | 相信模型自行遵守权限 |
| Tool Runtime | 严格解析参数、限制批量、执行 allowlist、分派 handler | `tool_executor.py`、`tool_security.py` | 扩大 Planner 授权范围 |
| Approval | 将高风险操作挂起并等待一次性用户决定 | `tool_approval_service.py` | 持久化或重放旧批准 |
| Domain Handler | 执行具体读写，并校验对象归属 | `services/tool_handlers/*` | 自行修改全局工具政策 |
| Trace / Diagnostics | 记录 Planner、上下文、模型、工具、终态 | `agent_run_evaluation.py`、`agent_run_performance.py` | 保存密钥或完整敏感内容 |
| Evaluation | 确定性回归、人工质量评分、真实 Pilot | `agent_eval_*`、`agent_runtime_regression*`、`agent_run_review.py` | 用单一总分掩盖安全失败 |
| Release Control | 上线门禁、灰度对比、回滚建议 | `agent_release_control.py` | 自动修改生产部署状态 |
| Security Red Team | 验证宿主安全不变量 | `agent_security_redteam.py` | 宣称提示注入可以被彻底消除 |

## 3. 一次 Agent Run 的数据流

```mermaid
flowchart TD
    U[用户请求] --> C[宿主上下文预算与不可信数据封装]
    C --> P[Planner 生成规范化计划]
    P --> R[Run Controller 持久化 Run 与 To-dos]
    R --> S[按当前步骤缩小工具 Schema]
    S --> M[模型生成回答或结构化工具调用]
    M --> V{宿主校验}
    V -->|参数或权限非法| F[失败并记录 Trace]
    V -->|只读或提案| T[Tool Handler]
    V -->|高风险写入| A[一次性人工审批]
    A -->|拒绝/超时| B[Blocked 或安全结束]
    A -->|批准| T
    T --> R
    R -->|仍有步骤| S
    R -->|完成| D[Done + Diagnostics]
```

## 4. 三种不同的约束

| 约束 | 面向谁 | 示例 |
|---|---|---|
| 模型可见 Schema | 告诉模型“有哪些能力” | 当前步骤只展示 `getChapterContent` |
| Planner 步骤 allowlist | 告诉执行器“这一步允许哪些工具” | read 步骤不能调用 delete 工具 |
| Tool Policy | 宿主最终安全边界 | `deleteCharacter` 永远需要人工批准 |

三者不能合并。模型看不到某工具不等于后端安全；模型看得到某工具也不等于当前步骤允许执行。

## 5. Run 状态语义

| 状态 | 含义 | 是否系统故障 |
|---|---|---|
| `running` | 正在规划、推理、执行或等待事件 | 否 |
| `done` | 任务正确完成 | 否 |
| `blocked` | 等待/拒绝/业务条件不足，不能继续 | 通常否 |
| `canceled` | 用户断开或主动取消 | 否 |
| `failed` | Planner、模型、工具或安全不变量失败 | 是 |

终态不可被后续异步事件覆盖。断开连接必须持久化 `canceled`，避免僵尸 Run。

## 6. 信任边界

| 输入 | 信任级别 | 防护 |
|---|---|---|
| 用户提示词 | 不可信 | 不直接成为宿主授权 |
| 章节、记忆、大纲 | 不可信数据 | JSON 封装并注入宿主安全说明 |
| 模型文本 | 不可信 | 不从自然语言解析伪工具调用 |
| 模型工具参数 | 不可信 | 严格 JSON、大小限制、作用域校验 |
| 工具名称与调用 ID | 不可信 | allowlist、唯一性、批量上限 |
| 用户批准事件 | 有限信任 | 绑定一次 pending approval，消费后不可重放 |
| 后端 Tool Policy | 权威 | 默认拒绝未分类工具 |

## 7. 上下文策略

总窗口先扣除输出预留和工具 Schema，再由历史、记忆、关联章节/大纲动态申请预算。未使用预算可被其他区域借用。历史裁剪以完整对话轮次为单位。

上下文越多不一定越好。优化顺序是：先删除失败请求、重复轮次和无关 Schema，再考虑裁剪事实内容。

## 8. 评测与发布

| 信号 | 回答的问题 |
|---|---|
| 单元/集成测试 | 代码契约是否仍成立 |
| Runtime Regression | 历史事故是否重新出现 |
| Security Red Team | 宿主安全边界是否仍成立 |
| Pilot Case | 真实模型流程是否可用 |
| Human Review | 回答内容是否真正有用 |
| Performance Report | 慢和贵发生在哪里 |
| Release Gate | 是否有资格进入灰度 |
| Rollout Comparison | 应扩大、暂停还是回滚 |

## 9. 必须长期保持的不变量

1. 模型可见工具与后端 handler 必须契约一致。
2. 后端执行权限不能只依赖提示词或 Schema 隐藏。
3. 未分类工具不得启动或执行。
4. 高风险持久化操作必须经过一次性用户批准。
5. 工具参数解析失败必须 fail closed。
6. ID 型写操作必须校验目标属于当前书籍。
7. 不可信上下文不能改变工具权限或批准状态。
8. 每个 Run 必须绑定 `run_id`、发布版本和灰度组。
9. `blocked`、`canceled`、`failed` 必须保持不同语义。
10. 安全回归必须阻止发布，而不是被平均质量分抵消。

## 10. 当前已知边界

- Approval Broker 和供应商能力缓存是进程内状态；重启后安全失败关闭或重新探测。
- Token 数据目前包含工程估算，不等同于供应商账单 usage。
- 发布模块给出决策，不自动部署或回滚安装包。
- Prompt Injection 无法仅靠提示词完全消除，真正安全边界仍是宿主 allowlist、对象归属和人工审批。
