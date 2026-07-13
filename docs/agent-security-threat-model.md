# Agent 安全威胁模型

## 保护对象

- 书籍、章节、人物、设定、大纲与长期记忆。
- 用户对高风险写入的最终决定权。
- API Key、本地路径和内部错误细节。
- Agent Run、Trace 和发布归因的完整性。
- 本地后端的可用性。

## 攻击入口与控制

| 威胁 | 攻击入口 | 可能影响 | 宿主控制 | 剩余风险 |
|---|---|---|---|---|
| 直接 Prompt Injection | 用户提示词 | 诱导越权工具 | 步骤 allowlist、Tool Policy、审批 | 模型文本仍可能被操纵 |
| 间接 Prompt Injection | 章节、记忆、大纲 | 把素材中的命令当系统指令 | 不可信 JSON 封装、宿主权限不变量 | 回答内容可能受软性影响 |
| 工具名称伪造 | 模型 tool call | 调用未注册能力 | Tool Contract、handler registry、默认拒绝 | 无 |
| 非法参数降级 | 畸形 JSON | 默认参数导致错误操作 | 严格 JSON，失败关闭 | 无 |
| Book Scope 覆盖 | 模型传入其他 bookId | 跨书籍访问 | host bookId 优先且冲突拒绝 | 本地恶意进程仍可直接访问 DB |
| 全局 ID 越权 | 其他书籍的 memory/entity ID | 跨书籍修改或删除 | handler 对象归属校验 | 需持续覆盖新增 ID 工具 |
| 审批绕过/重放 | approvalId | 未授权持久化 | 仅 live pending 可消费一次，重启失败关闭 | 进程崩溃会丢失待审批操作 |
| 工具调用洪泛 | 单轮大量 tool calls | CPU、DB、审批队列耗尽 | 每轮调用数、参数、结果体、总轮数限制 | 合法慢工具仍需超时策略 |
| 协议 ID 混淆 | 重复/空 call id | 结果错配 | 整批唯一性校验 | 供应商协议异常会使 Run 失败 |
| 错误信息泄露 | handler/DB 异常 | 路径或密钥泄露 | 对外通用错误、日志保留类型、已知模式脱敏 | 新型秘密格式需补充模式 |
| 静默 Planner 降级 | 模型/供应商错误 | 绕过预期步骤 | Planner fail closed、Trace | 可用性降低但不牺牲安全 |
| 发布回归 | 新版本破坏边界 | 真实用户受影响 | 红队套件接入发布硬门禁 | 仍需真实灰度样本 |

## 安全原则

1. 模型、用户文本和检索内容都不是授权来源。
2. 提示词是行为引导，不是安全边界。
3. 任何解析失败、未分类工具、作用域冲突都失败关闭。
4. 高风险操作的审批绑定具体 live 请求，不能成为长期令牌。
5. 安全不变量采用零容忍门禁，不与平均质量或性能相抵消。

## 红队用例

`GET /ai/agent-security-redteam` 当前运行六个无内容、确定性的宿主边界测试：

- 畸形工具参数。
- 重复 tool call ID。
- 单轮工具调用洪泛。
- bookId 作用域覆盖。
- 敏感错误泄露。
- 检索内容间接 Prompt Injection 的信任边界。

跨书籍 ID 修改、审批重放和异常 handler 等需要数据库或异步流程，作为集成测试保存在 `backend/tests/test_agent_security.py` 与 `test_tool_approval_service.py`。
