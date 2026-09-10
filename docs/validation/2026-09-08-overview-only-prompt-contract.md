# 概览专用输出与生成提示词精简

## 实现

aggregate_story 新请求使用 stage=aggregate_story 与专用 submitAnalysisOverview。工具 schema 只允许 result.storyOverview，禁止 facts、craftCards 等字段；必须有 summaryMarkdown 和 evidence。提交处理验证引文可从上游范围恢复，执行器将已冻结 facts/craftCards 与概览组合，概览模型无法通过输出重写上游分析。既有分析提交工具保留给其他阶段，最终 Artifact 结构不变。

概览提示词只要求交代主线，优先输入事实，有具体缺口或矛盾时才按需读取观察，不再要求浏览全量观察、去重和归并。只读观察工具保留以支持缺口补读，未盲目禁止合理取证。

写作技法生成提示词升为 v1.3：以足以指导写作选择作为交付条件，确定性的路径、文件、版本校验交给工具；只修复具体校验错误。补读绑定到具体信息缺口，完整 E01 校准例子缩成一个抽象尺度示例。保留单入口、单文件可完整交付、必要时多文件、来源边界、版本冲突先重读、封存不等于发布等约束。开发计划 5.5 核心提示词和 fixtures 提示词快照同步更新，未改变测试原文或评判标准。

该变更处理重复工作和交付边界，不修改第二类语义准确性规则，也不宣称内容质量通过。预算仍为 16,384，推理仍 enabled。

## 验证

相关 83 项测试通过，含概览专用工具的合法提交、拒绝重写 facts/craftCards、缺失概览拒绝、分析完整流程与恢复。

真实测试使用已授权 E02、配置的 Kimi kimi-k2.6，独立目录 `/tmp/purra-051-overview-only-e02-verified`。第一次尝试在 fixtures 提示词摘要校验时停止（目录 `/tmp/purra-051-overview-only-e02`），没有请求模型；同步 v1.3 快照后重试。

## 结果

- 全量 check:agent-refactor 通过：前端 462 项、后端 2244 项，含类型与组件边界检查。git diff --check 通过。
- Root `run_f23b58082c1b41bb` 最终 failed，error=tool_input_invalid，不能记为全流程成功。
- 提取 `run_1fb22c4b8e404e9a` done；概览 `run_ce09ce3444934263` done，4 次调用生成总量 4,238、推理 2,213。对照前次 C/D 概览阶段 9,942 / 10,270 生成 token，本样本明显减少，但不是多次统计结论。
- 实际提交工具为 submitAnalysisOverview，result 唯一字段 storyOverview。对照提取单元和概览单元持久化产物，20 条 facts、13 条 craftCards 深度相等，未被概览改写；证据为 overview-preservation-check.json。
- 后续技法生成 `run_68ff670cb5fc459d` 连续两次缺少公开执行说明，getTechniqueDraft 被 public_progress_required 门禁拒绝、同批 listAnalysisObservations 随之取消，最终 tool_input_invalid。不是 model_output_truncated；未修改或绕过门禁，也未因本次失败增加重试次数。
- v1.3 生成提示词的全链路真实验收因此未通过，不能从概览成功推导最终技法可用或质量通过。
- 测试进程已退出；退出仍有既有 httpcore2 异步迭代器清理警告。未启动常驻服务或重启用户后台。第二类语义准确性未修改。
