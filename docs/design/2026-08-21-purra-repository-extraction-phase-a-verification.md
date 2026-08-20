# PurrA 独立仓库迁移：阶段 A 验证记录

> 日期：2026-08-21
>
> 结论：Monorepo 内公共边界和 wheel 消费路径已经收口；阶段 B 将以当前源码建立
> 干净历史的 MIT 公开仓库 `Lybrands/purra`。在完成发布前，
> `packages/purra` 仍是当前权威源码。

## 已完成

- PurrTypos 生产代码只从审定的 PurrA 顶层公共模块导入；
- 新增正向导入白名单，覆盖 Application、Domain、Infrastructure 和 Router；
- 宿主不再导入 `purra.run_state`、`purra.output.processor`、
  `purra.context_orchestration.compaction` 等实现模块；
- PurrA 通过窄公共入口提供计划规范化、输出处理和上下文压缩组合能力；
- `backend/main.py`、Web/Electron 开发启动和 PyInstaller 不再注入
  `packages/purra/src`；
- 开发环境通过 `pip install --no-build-isolation -e packages/purra` 使用标准包安装；
- CI 构建 wheel 后强制覆盖 editable install，再运行 PurrTypos 宿主测试；
- Web/Electron 后端资源从构建出的 wheel 展开 PurrA，不再复制包源码目录；
- wheel 构建产生的临时目录和 `packages/purra/build` 会在资源准备后清理。

## 公共边界处理

本阶段没有把整个 `RunStateMachine` 暴露给宿主。PurrTypos 原先只需要它对
`ExecutionPlan` 应用 Core 初始状态规则，因此新增窄函数
`purra.api.canonicalize_execution_plan`。

`ContextCompressionCoordinator` 与 `AgentOutputProcessor` 是 Composition Root 需要的
通用能力，分别从 `purra.context_orchestration` 和 `purra.output` 惰性导出。惰性导出
避免 `ports -> output/context -> cancellation -> ports` 的初始化循环。

Artifact、Output、Long Task 和 Projector 的宿主导入统一经过对应能力模块根，避免
把 `contracts.py`、`ports.py`、`processor.py` 等文件布局变成跨仓兼容承诺。

## 验证结果

- `npm run check:purr-components`：通过；
- `npm run typecheck`：通过；
- 前端/Electron 单元测试：387 passed；
- PurrA 与 PurrTypos 后端全量测试：1735 passed；
- PurrA 结构行数预算门禁：通过；
- `npm run prepare:backend-resources`：成功构建并展开
  `purra-0.1.0-py3-none-any.whl`；
- 从 `build-resources/backend` 中的 wheel 安装结果运行全部 PurrTypos 后端测试：通过；
- 干净虚拟环境只安装 wheel 后运行
  `packages/purra/tests/installed_distribution_smoke.py`：通过；
- 生产代码私有 PurrA 导入扫描：无违规；
- PurrA 源码路径注入扫描：除 CI 的反向断言文本外无命中；
- `git diff --check`：通过。

第一次全量后端回归曾因 `purra.ports` 增加公共导出而超过既有文件行数上限 4 行。
没有抬高上限；压缩导入声明后重新运行全量后端测试，1735 项全部通过。

## 尚未完成

- 尚未创建或推送独立 PurrA 远端仓库；
- 按公开审计结论不迁移原 Git 历史；
- 尚未确定 PurrTypos 的外部依赖锁定与发布制品地址；
- PurrTypos 尚未锁定外部 PurrA tag、commit 或 wheel 校验值；
- 尚未删除 `packages/purra`；
- 本阶段未使用真实 Provider 凭据执行 E2E。模拟 Gateway 与完整协议测试通过，但在
  PurrTypos 产品发布前，真实 Provider E2E 仍是发布阻塞项。

## 下一阶段入口

阶段 B 已确认使用公开仓库 `Lybrands/purra`、干净初始提交和 MIT 许可证。
PurrTypos 第一版依赖固定 Git commit，还是带 SHA256 的 wheel 制品，将在阶段 C
开始前决定；不得在当前 PurrTypos 工作仓重写 Git 历史。
