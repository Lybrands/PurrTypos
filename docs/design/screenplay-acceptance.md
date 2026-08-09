# 剧本 Agent V2 确定性验收

## 目标

这套验收不评价模型文案质量，也不调用外部模型。它冻结重构期间必须保持的 Project、Operation、Revision、Head、Candidate、幂等和事务边界。

运行：

```bash
npm run test:screenplay-acceptance
```

该入口只运行 Native V2 测试，不再引用已经删除的 v1 Document 场景。

## 覆盖集合

- `test_screenplay_project_aggregate.py`：Stage 与 nextActions 只由权威 Heads 派生；
- `test_screenplay_v2_schema.py`：公共请求 DTO、枚举和输入校验；
- `test_screenplay_v2_routes.py`：项目、会话、Workspace、Operation、Working Copy、Revision、Accept 与幂等命令；
- `test_screenplay_v2_agent_projection.py`：Run/Artifact/Long Task 到一个 Candidate Revision 的原子投影与归属；
- `test_screenplay_v2_persistence_boundaries.py`：旧运行时文件不会恢复，V2 Repository 不回退旧 CRUD。

## 必须保持的业务不变量

1. 新项目从 Working Copy 开始，不制造假的初始 Revision；
2. 相同 Idempotency-Key 与相同请求返回同一回执，不产生重复 Project、Operation 或 Revision；
3. Operation 在 Run 之前持久化；Root Run 通过不可变 RunBinding 归属 Operation，Artifact 与 Long Task 只通过通用 lineage 关联；
4. 一个 Operation 最多产生一个 Candidate Revision；
5. 对话和 SSE 只暴露 Revision 引用，不复制 Proposal 全文；
6. Accept 只切换 Head，不复制 Revision；
7. 上游 Head 更新会在同一事务中失效下游 Head；
8. Project revision 与 Working Copy revision 使用各自的 CAS；
9. 重放、并发冲突或事务失败不留下半完成 Candidate；
10. 旧 `screenplay_documents`、v1 runtime 路径和通用 runtime `operation_id` 业务列不能重新进入运行时。

## 与重构门禁的关系

本验收回答“剧本业务结果是否仍正确”。Core 的 Run 生命周期和跨层依赖由以下入口分别验证：

```bash
npm run check:agent-refactor-boundaries
npm run check:agent-refactor
```

只有新对话链路通过完整门禁后，才允许删除旧剧本聊天实现。
