# 三个 Agent 退役后范围复核

日期：2026-09-14。

## 结论

小说创作、小说分析和剧本 Agent 的本轮重构已经达到退出条件：生产新建入口只安装
三个 PurrA-native profile；旧 executable source、工具目录与冻结机制已经删除；历史
identity 只进入 versioned query/tombstone 或明确命名的只读 adapter。当前没有证据支持
继续增加 Agent 框架层、兼容 facade、双写或新的恢复协议。

这不是“所有产品和发布工作都完成”。源码重构、真实模型行为、桌面验收、依赖发布和
正式分发是不同门槛，不能互相替代。

## 已闭合范围

- 三个 Agent 的 replacement 创建、持久 implementation identity、取消、恢复与历史回放。
- Writing 的背景/人物读取、宿主人物总数、代表性审批写入、关联上下文、语义记忆 fixture、
  技法自动选择、continuation source 与快照漂移失败关闭。
- Novel Analysis 的完整 recipe、审核发布、暂停/恢复、取消、编辑替换、引用回查与普通入口切流。
- Screenplay 的完整角色链、Candidate/Revision、取消/恢复、截断、历史回放、生产切流与旧源码删除。
- 完整 backend、TypeScript、串行 frontend、Electron 单测、Vite/Electron build、包内删除扫描、
  candidate wheel 摘要、应用签名与 DMG 完整性验证。

## 独立后续门槛

以下事项真实存在，但都不是继续 Agent 重构的理由：

1. **发布与分发**：当前 PurrA 1.0.0 是仓库内候选 wheel，本地 `.app` 为 ad-hoc 签名，
   DMG 未公证、未发布，代码也未 push/tag。版本统一、正式签名、公证和发布需要单独授权。
2. **真实 embedding Provider 兼容性**：Writing 语义记忆用 localhost 确定性 embedding fixture
   验证了宿主合同，没有可用配置时未擅自调用付费 Provider。只有准备支持具体 Provider
   或发布该能力时，才需要独立真实集成验收。
3. **Web 分发验收**：Vite 构建通过，最终交互纵切以 Electron 为主。若本次发布包含
   `dist-web`，应另做隔离浏览器 smoke，不应把 Electron 结果外推到浏览器运行时。
4. **并发前端测试 runner**：默认并发运行在输出全部断言后没有自行退出；同一 505 项清单
   以 `--test-concurrency=1` 正常退出并通过。该现象尚未证明是产品缺陷；只有在干净环境或
   CI 可复现时，才建立独立测试基础设施调查。

## 明确不做

- 不为普通 Writing 对话发明 Durable Task resume；现有产品合同没有该入口。
- 不恢复 legacy create/execute、rollback 开关或旧工具 schema。
- 不因目录命名不够“统一”搬迁仍在工作的业务服务或复制公共逻辑。
- 不读取、迁移或改写真实创作内容来证明源码重构完成。

因此，本计划到此停止自动开发。下一项若要开始，必须来自明确的发布目标、可复现缺陷或
新增产品需求，并重新定义独立验收边界。
