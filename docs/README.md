# 项目文档

## 维护入口

- [三个 Agent 当前代码边界](../backend/agents/README.md)
- [共享对话与执行规范](design/shared-agent-conversation-contract.md)
- [三个 Agent PurrA-native 重构计划与迁移账本](design/2026-09-12-three-agent-purra-native-rebuild-plan.md)
- [模型请求公共入口与适配规范](design/2026-09-06-model-request-boundary-refactor.md)
- [写作技法 v1 契约](design/2026-09-07-writing-techniques-p0-contracts.md)
- [共享 Markdown 创作资料](design/2026-09-07-shared-markdown-creation-materials.md)
- [仓库文件与忽略规则](repository-hygiene.md)

## 历史证据

带日期的设计、迁移与验证文档只描述对应时点；其中出现的旧路径、旧端点和
“当前实现”均不自动代表现行代码。`backend/agents/*/LEGACY_CONTRACTS.md`
保留退役前的只读合同快照，用于解释迁移与历史数据，不是可执行入口或兼容承诺。

## 内容边界

`design/` 保存领域契约、设计决策及迁移方案；维护入口中的迁移账本负责声明
replacement 的现行状态。`superpowers/specs/` 和 `superpowers/plans/` 保留早期
设计及独有的技术说明，用于追溯。

`validation/` 保存注明输入、环境、结果和限制的验收记录。历史通过只适用于记录中的版本与样本，失败结果和未覆盖项也应保留。`fixtures/` 保存验证脚本使用的合成材料；原始测试样本不能为了改善文案而改写。

个人笔记和本机审计快照放在忽略的 `local/`。共享文档不记录聊天经过、提问选项、给助手的启动提示、逐步提交指令或重复任务清单。技术文档直接说明问题、设计、行为和验证依据；过时方案注明替代关系，重复内容合并到同一维护位置。
