## Task 10 report — 完整门禁与真实 Provider E2E

### Delivered

- 将原先的 Provider smoke + 直接 finalizer fixture 改为真实 Agent composition、Screenplay
  service、SQLite 和 Provider gateway 组合测试；没有 Fake provider、生产 monkeypatch 或
  Task10 专用生产分支。
- 四个模型 profile 均保留独立 paid smoke。DeepSeek V4 Flash reasoning-off 额外承担完整工作流：
  answer（1 Root/0 Operation）、formal manual cancel（1 Root/1 Operation）、formal pause +
  continuation（同 Operation、新 Root）、Candidate、重复 finalization 和 lineage usage。
- manual cancel 在已应用 checkpoint 且 Root active lease 后触发，验证 Root/Child/LongTask/unit/
  delegation/artifact claim/host receipt/cancellation receipt 全部 drain。
- continuation 验证旧 Root events/todos 不可变、新 Root 完成、已完成 Recipe unit 的 attempt 与
  output ref 不变；普通 AI Part Child depth=1、role=`screenplay-part`、delegationId=null，确定性
  Part 不创建 Child，当前业务 registry 下 Delegation 总数为零。
- 公开 Root todos 只允许业务语义步骤；拒绝 Recipe、validation、publish、revision、Artifact、
  LongTask 和 plannerStepId 等私有标记。
- Candidate publication 使用生产 finalizer receipt 重放，重复请求仍只有一个 Revision 与一个
  authoritative candidate outbox event；Operation usage 只来自 Root/Child lineage，按 runId 去重。
- 新增静态边界 gate，阻止 paid E2E 再次引用 `_finalization_fixture` 或 monkeypatch，并要求使用
  `ScreenplayAgentService` 与 `create_agent_composition`。

### Controlled pause disclosure

完整 case 的第二个 formal Turn 使用持久 `ScreenplayCheckpointRepository.pause()` 注入
`REQUIRES_RERESOLUTION` outcome，从而稳定覆盖显式 continuation。这个 pause cause 是测试控制的
模拟约束变化，不是一次真实 Provider 自主规划出的 requires-reresolution；Root 初始计划、pause
前 AI Parts，以及 continuation 后 checkpoint/剩余 AI Parts/Candidate/final response 仍要求真实
Provider。测试和报告均不把该模拟 pause 描述为 Provider 能力证明。

### TDD evidence

- RED：`test_paid_screenplay_e2e_uses_the_real_root_workflow` 在旧测试上因
  `_finalization_fixture` import 失败。
- GREEN：边界测试改为解析 E2E AST，真实 E2E collection 保留四个 paid case；缺凭据时只在
  `_api_key_or_skip` 精确阻断对应 profile。

### Verification

- `PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run check:agent-refactor`
  - backend: `1848 passed, 5 skipped`
  - architecture boundaries: `58 passed`
  - Screenplay acceptance: `223 passed`
  - frontend unit: `347 passed`
  - typecheck/proposal/session gates: passed
- `PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run test:screenplay-acceptance`
  - `223 passed`
- `PURRTYPOS_PYTHON=/Users/liuyubin/Lybrand_project/PurrTypos/.venv/bin/python npm run test:screenplay-real-e2e`
  - `4 skipped`: missing `DEEPSEEK_API_KEY` (2 profiles), `ZAI_API_KEY`, `MIMO_API_KEY`
  - each skip is a precise `RELEASE BLOCKER`; no paid workflow was executed locally
- `git diff --check`: passed

### Release status

Code and credential-independent gates pass. Release remains blocked until the four paid Provider profiles are
executed with their real credentials; especially the DeepSeek reasoning-off comprehensive workflow has not been
observed live in this environment.
