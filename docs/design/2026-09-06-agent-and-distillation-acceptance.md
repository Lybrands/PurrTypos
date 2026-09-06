# 公共模型入口后的三 Agent 与 Skill 蒸馏验收

日期：2026-09-06（Asia/Shanghai；数据库时间为 UTC）。结论：应用侧修复和确定性门禁通过；小说写作与剧本的实际对话成功；小说分析生成了完整待审核产物，但 Root 公开总结被 PurrA 0.5.0 阻断，不能称整轮成功。Skill 质量也未通过本次独立复核。

## 测试范围

使用新进程、独立 SQLite 和独立 Web 端口 18431。复用此前已获准发送的单章《第1章 清河桥》，2,225 字符。模型为已配置 GLM-5.3-Flash，明确设置思考开启、effort=low、单次生成上限 10,000。实际 38 条 SDK 诊断均到达 provider_response_received；37 条流式、1 条后台非流式，全部发送 low/10000。非流式后台生成不用于公开对话文案。

临时数据库目录：`/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-acceptance-jv47rvbb`。没有保存到正式来源分析或方法库，没有改写正式书籍、章节、剧本。正式设置只读，正式服务重启前确认无 active Run 并已有 SQLite 备份。

## 实际对话

| 场景 | Run | 实际结果 |
|---|---|---|
| 小说写作，全局对话约 400 字新场景 | `run_e19ca887e36e4102` | done；19 条公开 commentary delta，352 条 final delta_batch |
| 小说写作，连续追问扩展 | `run_ee0f28bc475e40e5` | done；784 条 final delta_batch；浏览器在尚有停止按钮时已显示正文，最后一段停在逗号处，之后继续完成 |
| 剧本，约 300 字冲突建议 | `run_9f4156cbf7af4055` | done；25 条公开 commentary delta，302 条 final delta_batch；浏览器先看到执行说明再看到最终回答，未生成项目文档 |
| 小说分析，完整蒸馏 | `run_d5a4eccbaf304848`，恢复 Root `run_f51128d674224104` | 分析单元全部完成并有待审核产物；Root 总结失败，见下文 |

delta_batch 数字是持久化标准事件数，不是 token 数或浏览器绘制次数。分析各 Run 合计 1,131 条公开 commentary delta；执行中浏览器持续显示单元说明与真实工具完成事件。以上验证不等于每种模型、所有任务分支都已完成浏览器验收。小说写作的浏览器停止尝试发生在完成后，没有成功执行停止，因此本轮不声称浏览器取消通过。

输入保留领域差异：写作使用书籍下的全局对话，剧本使用项目探索对话，分析使用锁定来源章节与启动确认；它们共享模型配置和公开事件链路，不共享业务工具或交付物。

## 本轮修复

- 证据范围恢复：聚合生成上游摘录的连续原文子串时，继承同 section 的宿主范围；仍拒绝改写、拼接、跨章节引用及模型伪造范围。提交工具先验证再存 Artifact，最终原文逐字匹配校验继续保留。
- 提交契约：根据既有阶段 schema 派生必填字段路径，在绑定输入上下文中提供 submissionContract。模型必须提交完整替换结果，避免把 revisionNotes 放入 writingSkill、漏掉 purpose/tags。没有降低 schema 校验。
- Web 构建：build:web 使用根路径 `/` 的 asset base，修复 BrowserRouter 深链接刷新加载错误；Electron 仍使用原有相对资源路径。
- 恢复显示：历史失败单元保留的 errorCode 不再把当前 running/completed 单元显示为失败。但 Long Task 完成后 Root 总结仍可能失败，该真实错误必须继续显示。

## 蒸馏阶段与实际产物

任务 `longtask_e6b3c47cd09449d79df4b2965d2a2269`：11/11 单元 completed。提取、归一、聚合、证据校验原输出保留，attempt 均为 1。第一次 Skill draft 因缺少必填字段失败；修复提交契约后仅从失败处恢复，draft attempt=2，随后试写、修订、再试写和复评完成。

最终 Artifact：`novel-analysis-artifact://568d5c897c7b43f88e451788c54aa051`。包含 1 个故事概览、3 条事实、2 条来源机制观察、1 个有 6 步执行方法的 Skill、两轮各 2 个试写与模型复评。skillReviewStatus=pending_review，未发布。

完整模型原始方法及试写见[Skill 样本](../validation/2026-09-06-writing-skill-sample.md)。浏览器已实际打开故事概览、写作方法与迁移检验页，产物可读。

提取阶段经历过一次 upstream_stream_interrupted 后重试，最终完成。没有复现此前思考占满 10,000 Token 的预算耗尽，但一次成功样本不能证明该风险在全部模型和设置下消失。

## 未通过的部分

### 公开总结

Root 在 completion_projection 失败：model_reasoning_mode_conflict。PurrA response transaction 构造模型调用时遗漏 reasoning_mode，默认 DEFAULT 与 Run 的 ENABLED 冲突，尚未进入 SDK。详见[框架最小复现与交接](2026-09-06-purra-response-reasoning-handoff.md)。不修改框架源码或发布包来冒充下游适配修复。

### 写作质量

模型复评五项全部标为 passed，但独立阅读产物发现以下反例。这些是本次质量不通过的依据：

1. 修订后试写一要求“不可解释的异象”，正文仅描写灰衣人向空墙鞠躬；这可由普通行为解释。摸到正常护栏也不能验证异象存在。评估仍判定“物证”与首登场目标达成，证据不足。
2. 修订后试写二的 stepApplications 声称“当场查看与询问收银员”，应用正文没有询问收银员；该动作反而在 baseline。说明评估可能混用了对照文本与应用文本，未逐项核对实际正文。
3. 首轮两组 baseline 均为 177 字符、application 均为 362；第二轮分别为 186/300、191/235。对照篇幅并未严格控制，不能把多写的细节直接解释为 Skill 带来的质量提升。
4. “明天能否回到原地：能就不适用”“最多三步事故、异象只能一句”等被写成刚性规则，但只有一章与同模型自写、自评的小样本支持。它们最多是当前方法的候选启发式，尚不足以推导必要条件。

下一轮质量设计应先固定独立 brief 与对照篇幅，再逐条检查应用正文是否执行步骤、是否保持人物与事实约束；评审不能把模型自述作为完成证据。需增加独立评审和不同来源样本。本轮未用手工改好的小说替换原始试写，也未将五项模型通过包装为成熟度结论。

## 验证与运行状态

`npm run check:agent-refactor`：后端 2,129 项、当时前端 441 项，类型和组件边界检查通过；随后恢复状态修复新增 3 项前端测试，全量 typecheck/test:unit 再次通过，前端共 444 项。最终 `npm run build:web` 通过，`git diff --check` 通过。框架诊断脚本明确报告 4/6 冲突，是已知外部阻断，不能记作通过。

正式开发后端已重启加载应用修复，原前端开发服务继续运行。临时验收服务及测试进程在收尾时停止；保留 SQLite 与文档作为原始失败和产物证据。未构建 Electron/Windows 安装包，未发布任何包。
