# 三个 Agent 安全威胁模型

## 保护对象

- 书籍、章节、人物、设定、大纲、分析证据、剧本 Artifact/Revision 与长期记忆。
- 用户对高风险写入和产品 proposal 的最终决定权。
- API Key、本地路径、内部错误与私有 Tree Child 内容。
- Agent Run、事件、绑定、来源归因和恢复账本的完整性。
- 本地后端及 Provider 容量租约的可用性。

## 攻击入口与控制

| 威胁 | 攻击入口 | 可能影响 | 宿主控制 | 剩余风险 |
|---|---|---|---|---|
| 直接/间接 Prompt Injection | 用户文本、章节、分析来源 | 诱导越权或污染回答 | 不可信内容封装、profile allowlist、对象归属校验 | 模型文本仍可能被软性影响 |
| 工具名或参数伪造 | 模型 tool call | 调用未注册能力或错误写入 | 代码化 catalog、严格 schema、默认拒绝 | 新工具必须补齐边界测试 |
| Book/Revision scope 覆盖 | 外来 ID | 跨书、跨版本读写 | host binding、scope 与 revision lock | 本地恶意进程仍可直接访问 DB |
| 审批或 proposal 重放 | approval/proposal ID | 重复或未授权写入 | live ownership、一次消费、revision/digest 检查 | 崩溃窗口需靠持久恢复验证 |
| 幂等键混淆 | 重试、重复 call ID | 副作用重复或 Artifact 冲突 | operation attempt、tool idempotency gateway、writer claim | 新阶段必须定义稳定键 |
| 工具调用洪泛 | 大量调用或大结果 | CPU、DB、Provider 容量耗尽 | 轮次/调用/结果预算、超时、provider lease | 合法慢调用仍会占资源 |
| 私有结果泄漏 | Tree Child、诊断、错误 | 暴露素材、路径或密钥 | 规范事件投影、公开/私有边界、错误脱敏 | 新投影器需持续审计 |
| 传输断开误取消 | SSE disconnect | 已完成副作用被误报或任务丢失 | 订阅与 durable Run 分离、显式取消 | UI 必须正确恢复快照 |
| Legacy 回退 | 历史 identity/旧数据 | 绕过现行控制 | legacy execute fail closed、旧源码删除、只读 tombstone | 查询路径仍需防止执行化 |
| 发布回归 | 新版本或错误 wheel | 安全边界与行为漂移 | 锁定 wheel/hash、完整测试、隔离 Electron 验收 | 仍需独立真实 Provider 验收 |

## 安全原则

1. 模型、用户文本、检索内容和历史 Run 都不是授权来源。
2. 提示词是行为引导，不是安全边界；授权由宿主代码决定。
3. 解析失败、未知工具、作用域冲突和不明副作用一律失败关闭。
4. approval 与 Writing proposal 是不同合同，均不能成为长期授权令牌。
5. 取消、重试和恢复必须服从已提交事实，不能覆盖或伪造终态。
6. 历史合同只可读取，不得通过兼容层重新获得执行能力。

## 回归证据

安全红队不再通过 HTTP 诊断端点运行。当前确定性边界由以下测试族维护：

- `backend/tests/test_purra_tool_security.py`：工具 schema、allowlist、批次与输入安全。
- `backend/tests/test_sqlite_tool_idempotency_gateway.py`：持久幂等、重放与并发所有权。
- `backend/tests/test_agent_refactor_boundaries.py`：replacement/legacy 单路径边界。
- `backend/tests/test_writing_replacement_read_tools.py`：Writing 读取 scope 与结果合同。
- `backend/tests/test_novel_analysis_replacement_artifacts.py`：分析 Artifact 所有权与冲突。
- `backend/tests/test_screenplay_replacement_artifacts.py`：剧本 candidate Artifact 与 attempt。

增加工具、scope、Artifact 类型、Revision 写入或公开事件字段时，必须在对应产品测试中增加
越权、重放、畸形输入和失败关闭用例。测试通过只证明确定性宿主边界；真实 Provider 的
工具选择、输出质量与 Electron 展示必须单独验收。
