## Task 10: 完整门禁与真实 Provider E2E

Status: COMPLETE

### Objective

用真实 Provider、真实 Agent composition 和 SQLite 持久化完成 Screenplay Root Run 最终验收。
覆盖 answer、formal create、checkpoint revision、manual cancel、continuation resume、Candidate
生成与重复 publish；缺少凭据或网络时只允许精确标记 `RELEASE BLOCKER`，不得用 Fake Gateway
冒充真实验收。

### Audit findings

- 现有 `test_screenplay_multi_model_e2e.py` 只验证 Provider 文本/JSON smoke，并直接调用
  `_finalization_fixture()`；它没有经过 Screenplay Root planning、task admission、Recipe、Child、
  checkpoint、cancel 或 continuation。
- Writing real E2E 已有可复用的安全模式：运行真实 composition/DB、通过
  `ProviderModelGateway` 预检、有限超时、精确凭据 skip，并从持久化 Run/event/tool state 断言。
- Task5-9 的模拟/持久化测试已分别覆盖 lifecycle 细节；Task10 不新增生产抽象，只把这些
  契约串进 paid release gate，并保持现有四个 Provider profile 的模型协议 smoke。

### TDD sequence

1. RED：把 real Screenplay fixture 改为真实 project/composition/service，新增 answer 与 formal
   workflow 断言；旧 provider-smoke/finalizer-only 路径不能满足新断言。
2. RED：formal create 必须产生一个 Root、一个 Operation、公开语义计划、Candidate 与至少
   一次 checkpoint revision；普通 AI Part Child `delegation_id` 为空，确定性 Part 无 Child。
3. RED：在 live formal 执行中手动 cancel，等待权威 Root tree terminal，断言 Run/LongTask/
   unit/delegation/lease/claim 无 zombie。
4. RED：显式 continuation resume 产生新 Root，复用同一 Operation/Recipe，不重做 completed
   Part；旧 Root 与其 events/todos 保持不可变。
5. RED：publish 使用真实持久 finalizer/Revision receipt，重复请求返回同一 Revision；usage 按
   Root lineage 去重汇总。
6. GREEN：只复用现有 Screenplay service、cancellation、continuation 和 finalization入口；
   不新增 Fake provider、monkeypatch 或生产分支。
7. VERIFY：先跑静态 collection/skip/fixture 与 focused tests；再跑
   `check:agent-refactor`、`test:screenplay-acceptance`、`git diff --check`。有凭据才实际运行
   `test:screenplay-real-e2e`，否则逐 case 报 RELEASE BLOCKER。

### Scope watch

- 当前 Screenplay 未注册 agent roles，因此 E2E 期望零 Delegation；显式 role 的通用契约沿用
  既有独立测试，不为本验收造业务 role。
- 公开 TaskPlan 不得包含 Recipe、validation、publish、unit 或内部 Artifact 标题。
- 不读取或输出 API key；Provider 异常只报告异常类型。
- 本 Task 只改验收测试与 SDD 报告；若真实链路暴露生产缺陷，先写稳定 RED 并报告再修。

### Result

- paid E2E 现以真实 `create_agent_composition`、`ScreenplayAgentService`、SQLite 和
  `ProviderModelGateway` 为唯一入口；静态边界测试会拒绝 `_finalization_fixture` 与
  `monkeypatch` 回流。
- DeepSeek reasoning-off 是 comprehensive case：answer、formal cancel、typed pause、
  continuation、Candidate、finalization replay 与 usage lineage 在同一真实业务工作流中验收；
  其余三个 profile 保留真实 Provider smoke。
- cancel case 等待 checkpoint 已应用且 Root 持有 active lease 后才走控制面取消；resume case
  的 `requires_reresolution` 原因由测试通过持久 checkpoint repository 注入，是确定性的测试控制
  pause，不冒充 Provider 自主产生。pause 后的 continuation、checkpoint、Child、Candidate 与
  finalization 仍全部使用真实生产链和 Provider。
- 本地没有四组 Provider 凭据，因此 paid branch 未实际执行；四个 case 均以精确
  `RELEASE BLOCKER` skip 报告，不能视为发布通过。
