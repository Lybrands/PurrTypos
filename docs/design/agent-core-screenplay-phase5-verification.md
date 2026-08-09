# Agent Core 与剧本 Agent Phase 5 验证报告

> 日期：2026-08-09。范围：Phase 0–5 最终架构门禁、旧数据清理和故障注入。这里记录的是确定性宿主行为，不评价外部模型文案质量。

## 1. 运维结论

应用启动时只恢复原生 V2 状态，不迁移旧剧本 Agent 数据：

- `source_snapshot_json IS NULL` 的旧项目按项目所有权闭包删除；
- v1 Document、分集、来源关联、迁移 link/report 表直接删除；
- 通用对话中的旧 Proposal/Revision 列直接删除；
- 通用 Run、Work Item、Long Task、Artifact 的 `operation_id` 列和索引直接删除；
- Book、Outline、Article、人物、世界设定和非剧本 Agent 数据不在清理范围。

运行时只接受以下归属链：

```text
Screenplay Operation
        ↑ exact immutable RunBinding
Root Run ── root/parent lineage ── Child Run
   │                                  │
   ├── Work Item links                ├── Artifact lineage
   └── Long Task creator/work item ───┘
```

没有后补绑定、Session 猜测、旧列回填、自动创建兼容 Operation 或双轨读取。Artifact 投影完成状态单独写入 `ai_agent_artifact_projections`，不污染通用实体。

## 2. 故障注入矩阵

| 故障/竞争 | 期望行为 | 确定性覆盖 |
|---|---|---|
| 重复提交相同 Proposal | 一个 Operation、一个 Candidate Revision；重放返回同一引用 | `test_agent_proposal_atomically_creates_one_replay_safe_candidate` |
| Proposal 引用不存在的 Artifact | Proposal event 与 Candidate 同时回滚；Operation 保持 running | `test_proposal_event_and_candidate_roll_back_together_on_bad_runtime_ref` |
| Run 缺失或错绑 Operation | `409`，不收养 Run、不写兼容字段 | projection/binding 回归与 schema ratchet |
| Operation 启动时捕获的 Head 后来变化 | Candidate 仍使用启动时的输入快照 | `test_operation_candidate_keeps_captured_upstream_head` |
| Pause/Cancel 与子 Run/Long Task 并存 | 从 Binding 与 lineage 计算运行树并统一 checkpoint/terminalize | `test_operation_control_checkpoints_the_entire_runtime_tree` |
| Root Run 已终态但 Operation 仍 running | 启动/监控 reconciliation 把 Operation 与运行树收口 | `test_terminal_root_reconciliation_pauses_operation_and_runtime_tree` |
| Root Run 完成但没有 Candidate | Operation 明确失败，不伪造业务结果 | `test_root_run_completion_without_candidate_fails_operation` |
| 进程在 Operation running 时退出 | 下次启动把遗留 Operation checkpoint 为 paused | `test_lifespan_startup_checkpoints_running_screenplay_operation` |
| 同一 Turn 被并发执行 | lease 只允许一个模型 Run | `test_turn_execution_claim_prevents_duplicate_model_runs` |
| Turn/Operation 原子创建中校验失败 | User Turn、Operation、receipt 全部回滚 | `test_operation_validation_failure_rolls_back_turn_and_receipts` |
| Resume 重复提交 | 幂等，不重复 Run，不持久化密钥 | `test_resume_command_is_idempotent_without_persisting_credentials` |
| SSE 订阅断开 | 只移除订阅者，不取消 Turn/Run/Operation | `test_sse_disconnect_removes_only_the_subscriber` |
| cursor 重放或倒退 | cursor 单调前进，Snapshot 仍为权威事实源 | `test_conversation_event_cursor_is_forward_only` |
| 删除 Project | 删除聚合所有权闭包，保留通用 Run 审计和不可变 Binding | `test_deleting_v2_project_cascades_aggregate_but_preserves_run_audit` |
| 打开含 v1 表/列的数据库 | 删除旧剧本数据与字段，Writing 数据不受影响 | `test_startup_retires_legacy_screenplay_store_without_touching_writing_data` |

## 3. 自动门禁

最终入口：

```bash
npm run check:agent-refactor
git diff --check
```

`check:agent-refactor` 依次验证：

- 26 项静态架构边界；
- 40 项原生剧本 V2 验收；
- TypeScript typecheck；
- 130 项前端/桌面单元测试；
- 1310 项后端测试。

架构 ratchet 额外保证：

- 通用 `AgentComposition` 的 screenplay 引用为 0；
- 通用 Agent DTO、Router、SSE、Conversation 和前端 reducer 的 screenplay 引用为 0；
- 剧本 Domain 对 Database/Infrastructure/Application 的反向依赖为 0；
- 通用 runtime repositories 的 `operation_id` 引用为 0；
- 已删除的迁移请求模型和 Phase 4 兼容模块不能恢复。
