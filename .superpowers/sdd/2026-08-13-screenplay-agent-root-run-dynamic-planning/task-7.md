## Task 7: 在业务里程碑修订 Root 公共计划

Status: PASS

### Objective

让 Screenplay durable execution 只在 episode 完成、document section batch 完成、
review aggregate 完成三个业务检查点调用 `ScreenplayCheckpointPlanner`。合法结果作为
`LongTaskExecutionUpdate.plan_revision` 交给 PurrA Core 发布；业务代码不直接发布
todo 事件，也不为修订另建公开 Run。

### Required contracts

- planner 输入只含原 Root `TaskPlan`、已完成步骤摘要、Artifact receipts、typed
  failures / constraint changes 和 remaining scope；不含正文、LongTask/WorkItem/unit/
  Operation 等内部身份。
- planner 必须返回完整 revised `TaskPlan`，step ID 集合与原计划完全一致。
- 只允许修改 future step 的 title、description 和 dependency order；completed
  steps、phase、deliverable、scope、base Revision 与已产 Artifact receipts 不可变。
- scope、stage 或 deliverable 变化必须返回 `requires_reresolution`，并让 Operation /
  Turn 暂停、Root 以 paused/canceled terminal 收束，等待新 Turn 重解析。
- planner invalid、typed failure 或不可验证输出保留原计划并暂停；不得降级成成功。
- 合法修订只通过 `LongTaskExecutionUpdate.plan_revision` 进入 Task 1 的 Core durable
  revision contract；业务侧 todo event 数量恒为 0。
- 每个 Part 不触发 replan；同一 milestone 只调用一次且顺序稳定，live/replay 不重复。

### TDD sequence

1. RED：真实 dynamic provider fake 执行多 Part Recipe，断言只有三个业务 milestone
   调用 planner，次数和顺序正确；每个 AI/deterministic Part 不单独调用。
2. RED：锁 planner prompt/input privacy，只允许摘要、receipts、typed constraints 和
   remaining scope，不含正文及内部 IDs。
3. RED：合法 future-only revision 通过 update 进入 Core，live/replay 都看到相同
   revised plan，Root/Operation 数量不变且 Artifact 不覆盖。
4. RED：completed step 修改、step ID 变化、invalid output 和 typed planner failure
   均保留原计划并暂停，不伪装成功。
5. RED：scope/stage/deliverable/base Revision/已产 Artifact 变化返回
   `requires_reresolution`，Operation/Turn 暂停并由同一 Root terminal 收束。
6. GREEN：实现 business checkpoint observer/planner、持久 checkpoint identity 与
   pause mapping；复用 Core Task 1 revision validation，不复制 todo 编排。
7. 跑 durable/profile/compiler/executor/replay/cancellation 回归、边界检查与完整 gate。

### Scope watch

- 若 LongTask observer 不能把检查点决策反馈成 plan revision / PAUSED terminal，先定义
  最小通用反馈接缝；不得在 Core 引入 Screenplay 分支。
- 若 planner 需要模型调用，复用现有 composition/provider planning capability；不得
  恢复旧 screenplay intent planner、第二 Root 或独立 screenplay planning Run。
- Artifact receipts 只传公开/稳定引用与摘要，不传正文或 recipe unit identity。
- Task 8 的通用 resume/retry 收敛不在本任务提前实现。

### Progress

- AUDIT：Task 1 已提供完整 durable revision 验证、Core todo 发布和 checkpoint 持久化；
  Task 7 只需在 Screenplay composition boundary 产生 milestone update。
- AUDIT：Task 6 已把 AI Part 生命周期放入 host Child，并以 Recipe/Artifact 为权威；
  checkpoint planner 不得重新拥有 Child/Run terminal 或 Candidate 内容。
- GREEN：episode、document batch、review aggregate 是仅有的检查点；普通 Part 不触发。
- GREEN：持久 receipt 以 operation/checkpoint 唯一，CAS 单赢家、过期可接管；ready
  output 与 Root `planRevision` event 双向 reconcile，崩溃恢复不重复调用模型。
- GREEN：planner 只接收公开 Root 计划、摘要、无正文 Artifact digest、typed failure 和
  remaining scope；内部 task/run/unit/Operation/base Revision ID 不进入模型输入。
- GREEN：Root 首个/最新计划从持久 todo event 与 todo snapshot 权威读取；缺失、不一致、
  stale ready 全部 fail closed 并暂停。
- GREEN：合法 future-only revision 经 Core 发布；scope change/invalid/provider failure
  持久暂停 LongTask、Operation、Turn，并取消同一 Root。
- GREEN：durable observer ACK 使用 typed Future；成功、contract rejection 与 caller cancel
  都能结算并有界清理 dispatcher。
- VERIFY：Task7 三目标 210 passed；Core/SQLite/cleanup/boundary 77 passed；PurrA 91 passed；完整门禁 1754 backend + 344 frontend passed，5 个真实 Provider E2E 因缺凭据跳过并保持 RELEASE BLOCKER。
