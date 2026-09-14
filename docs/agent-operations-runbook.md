# 三个 Agent 单路径运维手册

## 1. 现行执行路径

小说创作、小说分析和剧本请求都从产品路由进入同一个组合根：

```text
HTTP / SSE route
  -> application/composition_factory.py
  -> PurrA AgentComposition / AgentRunService
  -> agents/{writing,novel_analysis,screenplay}
  -> canonical Run journal and query
  -> product projection / SSE presentation
```

生产环境只安装三个 PurrA-native profile。历史 identity 只用于读取旧 Run、事件和
tombstone；任何 legacy 执行请求都必须失败关闭，不能回退到旧执行器。

## 2. 生命周期

应用启动时初始化数据库、创建唯一 `AgentComposition`、安装三个 profile，并执行
统一的孤儿恢复。关闭时终止活跃执行、关闭待审批状态并释放数据库资源。

Composition 未就绪时请求应明确失败。不得临时创建第二个组合根、进程内旧任务表，
或以环境开关恢复已删除路径。API Key 只存在于调用范围，不得写入 Run、事件或日志。

## 3. 产品边界

- Writing 是响应式 Agent Run；只读工具直接返回，章节与材料写入产生可审阅 proposal，
  由产品接口应用或拒绝。
- Novel Analysis 使用 Root Run、durable task 和 unit 执行来源分析；暂停后保留已完成单元，
  恢复只继续未完成工作。
- Screenplay 使用 Root Run、阶段 operation、candidate Artifact 和 Revision；只有 Revision
  成功发布才形成正式完成结果。

三个产品共享 Run、取消、孤儿恢复、审批呈现和查询基础设施，但领域工具、Artifact、
Revision 与交付规则仍由各 replacement 目录拥有。

## 4. 传输、取消与恢复

SSE 断开只表示订阅者离开，不等于取消 durable Run。显式取消使用：

```text
POST /api/ai/agent-runs/{run_id}/cancel
```

前端重新连接后从规范事件和产品快照恢复展示。不要增加第二个读取
`http.disconnect` 的协程，也不要用内存 task registry 作为持久状态来源。

小说分析或剧本任务遇到可恢复错误、预算耗尽或需要用户动作时应进入产品定义的
暂停/阻塞状态；不得伪装成完成，也不得清空已经提交的单元、Artifact 或 Revision。

## 5. 审批与 Writing proposal

PurrA 高风险工具审批使用：

```text
POST /api/ai/tool-approvals/{approval_id}
```

审批必须绑定仍有效的 Run 和具体调用，只能消费一次。拒绝、过期或生命周期关闭时
不得执行工具；已提交事务不能被迟到取消伪装成未执行。

Writing 的 proposal 是产品级内容 diff，不是 PurrA approval。两者可以采用相似呈现，
但不能共享状态、事务或恢复语义。

## 6. 诊断与维护

单次 Run 诊断：

```text
GET /api/ai/agent-runs/{run_id}/diagnostics
```

开发模式还提供 planner、model-input 和 tool diagnostics。Artifact claim 的安全维护使用：

```text
POST /api/ai/artifacts/maintenance
```

维护入口只回收可以确认失效的 writer claim，不删除 Artifact、批次或 durable task。
旧 `/ai/agent-runtime-regressions` 与 `/ai/agent-security-redteam` 端点已经删除；回归与
红队检查由测试套件承担。

## 7. 常见故障判断

- `tool_not_authorized` / `tool_scope_violation`：检查当前 profile 的 catalog、宿主绑定和
  对象归属，不要放宽默认权限。
- `tool_input_invalid` / `structured_output_invalid`：属于模型输出合同问题，应在安全预算内
  重试；不得解析普通文本伪造工具调用。
- `provider_capacity_limited` / `upstream_stream_interrupted`：核对 provider lease 是否释放、
  是否已经公开内容或产生副作用，再按恢复策略处理。
- `approval_unavailable`：核对 approval 是否仍 live、是否已消费或是否在 shutdown 后到达。
- candidate/Artifact 冲突：先核对 operation attempt、idempotency key 和 writer claim，不能
  直接覆盖持久化内容。
- `paused` / `blocked`：先看 task、unit、attempt、output ref 与失败码；修正配置或输入后走
  正式恢复命令，不手工改终态。

## 8. 验证门槛

按改动范围运行定向测试，退休或公共边界变更至少执行：

```bash
.venv/bin/python -m pytest -q backend/tests
npm run typecheck
npm run test:unit -- --test-concurrency=1
npm run test:electron
npm run build:electron
git diff --check
```

真实 Provider、浏览器和 Electron 验收是独立门槛。使用合成书籍和隔离数据目录；验收后
停止所有测试进程并确认相关端口无监听。构建通过不等于 Provider 或真实作品验收通过。

## 9. 修改归属

- 组合根、请求映射与应用用例：`backend/application/`。
- 公共 Run、恢复、取消和呈现适配：`backend/agents/shared/`。
- 产品 Agent 合同与执行：`backend/agents/writing/`、`backend/agents/novel_analysis/`、
  `backend/agents/screenplay/`。
- Provider、SQLite 与通用宿主实现：`backend/infrastructure/`。
- Router 只负责 HTTP/SSE 边界，不实现产品执行状态机。

不得为临时故障重新引入双路径、legacy facade 或兼容开关。
