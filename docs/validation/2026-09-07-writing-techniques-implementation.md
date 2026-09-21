# 写作技法 1.0.0 实施与验收记录

日期：2026-09-07。分支：`feat/1.0.0`。对应[计划](../design/2026-09-07-writing-techniques-1.0.0-plan.md)。

状态：主要业务实现已落地；工程回归和隔离浏览器链路已取得通过证据，整体发布验收尚未通过。以下分开记录代码、模型生成质量、真实文件读取和发布依赖。未初始化、清理或恢复正式用户数据库。

## 1. 已接入的业务

- 技法统一以 `SKILL.md` 为入口，支持合法单文件及可选目录/辅助文件；YAML 元信息、相对引用、UTF-8、大小、路径与摘要校验采用同一契约。
- 正文与方案保存在本地 `writing-library/`，草稿支持批量修改、版本代次、操作幂等和恢复；封存与发布分离，已封存文件不可原地改写。数据库只保存业务状态及可重建目录索引。
- 技法库支持创建、编辑、历史版本、发布、归档、导入导出；方案引用准确版本，可携带成员导出。导入的成员先作为未发布草稿审阅。
- 默认仅手动指定。新会话继承小说默认值；会话模式独立持久化；发送/入队冻结本轮选择。仅手动无选择时不检索、补选或隐式注入旧主方法。
- 自动模式搜索已授权目录，真正选用后从入口读取；辅助文件按需读取。方案成员展开、版本冲突、预算、撤销授权和归档在后端生效，恢复不能绕过授权代次。
- 上传仅作为本轮受管理输入，不自动发布、入库或授权；可另存为独立库草稿。
- 来源分析升级为 schema 3，渐进式文件生成替代默认迁移试写链。按需读取观察和原文，提交结果为零个或一个技法，可有多个文件；正常材料不足不生成占位文件。生成结果保存为库草稿后仍需用户审阅发布。
- 结果弹窗固定高度并内部滚动，目录可切换文件；技法可返回来源分析。追问可只读该分析的固定版本文件。
- 写作回复的“本轮已读取”明细取自 `stream.opened.contextEvidence`，展示手动/自动来源及文件；准备状态和搜索候选不算实际读取。该凭据证明文件进入模型调用，不证明正文一定正确执行了写法。
- 旧六表及执行入口退役；历史分析、原作、小说、Memory、Run/事件保留。新备份 v2 包含文件与引用；旧备份在隔离候选库退役后启用；数据库与资源目录替换失败均回滚。

主要入口：`backend/application/writing_technique_*.py`、`backend/infrastructure/persistence/writing/`、`backend/routers/writing_techniques.py`、`backend/domains/writing_technique_prompts.py`、`src/WritingMethodsPage/` 和小说对话的技法选择/读取明细组件。前端既有目录名保留，产品用语统一为写作技法/写作方案。

## 2. 工程与浏览器验证

累计检查：`npm run check:agent-refactor`（组件规范、TypeScript、前端单测和全量后端）；最终次数及补充聚焦检查在本节末记录。`npm run build:web` 已成功生成 `dist-web` 并从公开源准备依赖；这不是 Electron 安装包或干净安装模型运行验收。

在 `/tmp/purrtypos-technique-ui-0907` 隔离数据、18327 后端与 5187 前端上验证：

1. 创建入口和 `场景/细节.md`、编辑、保存、发布、查看固定版本。
2. 创建并发布方案、管理小说授权、默认手动保持不变、手动选择方案。
3. 分析结果的故事概览/技法切换、保存分析、保存为技法草稿，草稿不自动发布。
4. 从生成技法返回来源分析页面。
5. 720 高视口弹窗为 672px；640×560 视口弹窗为 512px 高、616px 宽。目录切换不改变外框高度；正文内部滚动。

分析页面使用隔离合成产物进行 UI 验证，不将手工准备的产物称为模型生成。最终读取明细 API 另有回归测试，尚未通过真实对话页面逐项复验。跨平台文件系统和 Electron 原生窗口未验收。

本次浏览器标签及 18327/5187 服务已关闭，`lsof` 确认无监听；未停止用户原有的 18321 服务。

## 3. 原生写作模型读取

脚本：`scripts/verify-writing-technique-runtime.py`。模型为已配置的 Kimi-k2.6；文本和技法均为本次原创测试材料。最终证据目录：`/tmp/writing-technique-runtime-captured-0907`。

| 场景 | Run | 真实工具 | 实际模型输入 |
| --- | --- | --- | --- |
| 手动，未选技法 | `run_4aa56febd1354255` | 无 | 无入口、无辅助正文 |
| 手动，选择技法 | `run_f9e16ebb35ed4977` | `readWritingTechnique` | 入口及危险场景正文；无回忆正文 |
| 自动，已授权 | `run_1fbdc0f503ac4a0d` | 搜索→读入口→读危险辅助文件 | 入口及危险场景正文；无回忆正文 |

三次均为 `done`。后两例用户测试指令明确要求从入口按条件读取，证明原生工具与输入链路，不据此宣称所有自然语言任务都会自主选对文件。更早的自然措辞轮中，手动指定有入口凭据但没有读辅助文件，自动轮只检索未采用；相关原始报告保留在 `/tmp/writing-technique-runtime-0907`。该早期轮未开启输入正文诊断，旧报告中 `entryInActualInput:false` 的判定无效，应该视为未采集正文，不能据此断言入口未注入。

最终轮开启 `PURRTYPOS_DEV_DIAGNOSTICS=1`，逐调用核对 `stream.opened.callParameters[].inputMessages` 与文件凭据。没有以 Run 的 prepared entryRefs 代替真实模型输入。

## 4. 生成规范及内容质量

核心生成指引保持 P0 冻结摘要 `0230a92fc96cb7066325331648f6b05a86ce14ac3be12fadadf4e452ecc83883`。文件/工具要求与校准示例分别装配；实际 Artifact 记录提示词版本、核心摘要和完整装配摘要。v1.1 增加 E01 抽象尺度说明；v1.2 改用简短完整交付示例，仅使用预先登记的校准来源组 E01，不把 evaluation 案例答案放进提示词。

脚本 `scripts/evaluate-writing-techniques.py --validate-only` 校验 8 份来源、9 个案例及原文摘要/定位。生成者不接收 `judgeOnly` 或独立使用任务；独立使用采用新上下文。冻结文件不被执行结果覆盖。

v1.1 / Kimi 批次：`/tmp/writing-technique-eval-0907-kimi`。

| 案例 | 结构/原生提交 | 内容复核与覆盖 |
| --- | --- | --- |
| E01 | 已生成单文件，`run_ec2d959aa5bc4c7b` | fail：把短句聚焦扩成一句一信息等禁令，例子复用来源；U01 的局部写作安排可执行，不抵消技法缺陷 |
| E02 | 已生成单文件，`run_bccfc89fba9c42dc` | fail：把回忆/追逐的局部差异写成互斥禁令；U02a/b 可执行，多文件 not_covered |
| E03 | 已生成单文件，`run_91e7b02912f94f5b` | fail：保留误认→纠正机制，但强制不同感官通道并添加不相关伦理评估；U03 可执行 |
| E04 | 已生成单文件，`run_1587f4a0270f4f0f` | fail：重复观察合并后仍有禁止空间布局等过度规则，正文混入来源覆盖；U04 可执行 |
| E05 | 初轮引用失败；修复工具字段说明后 `run_b6aafae136374937` 成功 | fail：保留回避机制并排除无依据幽默，但例子复用了来源花盆/车票，增加两轮效果判断；U05 可执行 |
| E06 | 初轮引用失败；修复后 `run_e7c74ca5a5d745cb` 正常 insufficient_material | 无候选、无虚构文件；对应不足结果通过该例结构与内容边界 |
| E07a | 已生成单文件，`run_4c39eb0305e54438` | 双目录选择 not_covered：初版脚本预选了入口，不计为选择通过 |
| E07b | 引用失败；追加轮 `run_b64747e239ea4c73` 被公开说明契约拦截 | 未形成可用产物，双目录选择 blocked |
| E08 | 引用失败 | 未形成可用产物，独立使用未执行 |

E05 重试证据：`/tmp/writing-technique-eval-0907-evidence-ids`；E06：`/tmp/writing-technique-eval-0907-insufficient-fixed`；E07b：`/tmp/writing-technique-eval-0907-pair-fixed`。逐案例独立复核写入旁侧 `review.json`，没有覆盖生成文件。内容复核由 AI 执行，复核上下文独立于对应生成调用；没有第二位人工评审，也没有读者效果试验。

实测暴露并修复的宿主问题：来源读取结束位置合理越过 EOF 时按 EOF 截止；生成单元的模型轮次从 6 调至 16 以支持目录/读取/分批写入/提交；观察引用字段明确必须使用目录 ID/contentDigest，不能提交摘录或小节 ID；已标记 unsupported 的观察不能用于支撑 generated。保留原始失败，不把重试成功改写成初次成功。

DeepSeek 的早期尝试同样保留在 `/tmp/writing-technique-eval-0907-*`；部分输出截断/工具参数失败，部分成功生成但内容绝对化。没有跨模型稳定性、Token 节约或耗时改善结论。若干完成后的进程出现 `httpcore2` 异步生成器关闭警告；已完成 Run 与文件可核验，但 Provider 客户端清理不能声称完全无异常。

v1.2 E01 已生成（`run_580b4e1b7b4b494d`，`/tmp/writing-technique-eval-0907-calibration-v12`），正文更简短，但仍沿用来源具体表达，内容复核为 fail。完整 synthetic E02 流水线已完成：根 Run `run_0fc74729db294dd1` 为 done，Task `longtask_9c190a86747b4f76832b694dfc56388b` 五个单元全部完成，产物 `novel-analysis-artifact://2c9c7b3849c344358f2f2a8de2613006` 为 schema 3 / generated。固定版本 `d06f49b055fa4af773635dadc1ab1d0f762b458cb562c3c4d45d5592f0c92749` 为单文件，未发布。证据在 `/tmp/writing-technique-pipeline-synthetic-0907`，配置是 v1.1；两项独立使用已执行。内容复核仍 fail（例如把追逐短句规定为三到七字），多文件 not_covered。当前 B 线不满足整体验收；模型能提交合法文件不等于技法内容优秀。下一轮须重点约束从观察到创作条件的抽象尺度，并另补未用于调试的复杂来源，验证自然产生的多文件和分支读取。不能手工拆分当前单文件后宣称原生多文件已通过。

## 5. 原稿和发布依赖阻塞

后续更新：公开依赖已升级至 0.5.1，原推理配置冲突的离线、真实最终说明和完整 E02 流程复测通过，详见 [0.5.1 复测记录](2026-09-07-purra-0.5.1-retest.md)。本节下方的 0.5.0 结论保留为当时记录；原稿授权与内容质量边界未由依赖升级解除。

R01 已只读核验，观察与使用任务冻结于 `/tmp/writing-technique-r01-frozen-0907`；原文未放入 Git，尚未发给模型。自动审批拒绝向外部 Kimi 发送该原稿，理由是没有针对这份稿件及目的地的明确授权。已询问用户是否允许此次发送；未得到答复不能视为批准。`--synthetic-case E02` 使用仓库原创材料，不读取 R01，不能替代真实原稿验收。

公开包探针：

```sh
PYTHONPATH=build-resources/backend .venv/bin/python scripts/diagnose-purra-response-reasoning.py
```

公开 PurrA 0.5.0 的 direct/presentation × explicit enabled/disabled 共 4/6 准入失败，均为 `model_reasoning_mode_conflict`；default 两项通过，未调用 SDK。根 `.venv` 仍包含既有本地修复，故这份环境中的 Provider 成功不能替代公开安装验收。需要在独立 PurrA 仓库修复并发布可安装版本、更新应用锁定依赖后重新验收；验证未改动或发布该仓库，也未修改供应包绕过问题。

## 6. 最终检查记录

- `npm run check:agent-refactor`：通过；前端 458 项，后端 2,218 项（114.41 秒），组件规范和 TypeScript 通过。日志 `/tmp/technique-check-final-followup.txt`。
- 此后增加了 unsupported 观察准入测试，并调整 E01 校准提示词；相关 32 项聚焦测试通过，日志 `/tmp/technique-last-focused.txt`。不把聚焦数与全量数相加冒充新一轮全量结果。
- `npm run build:web`：最新代码及 v1.2 提示词构建通过，日志 `/tmp/technique-build-final-followup.txt`。公开依赖推理准入探针仍为 4/6 失败，见第 5 节。
- 冻结样本校验、文档相对链接校验、`git diff --check` 通过。
- 上述已启动的模型评估/完整流水线进程均已结束；后端、前端和浏览器测试资源已清理，测试端口无监听。模型脚本退出 0 只表示记录完成，其 report 中 failed 和 review 中 fail 仍为失败。

仍未通过或未覆盖：B 线内容质量、自然生成多文件、双目录选择、E08 独立使用、R01 外发后的真实来源验收、跨平台/Electron，以及公开依赖的最终响应。当前可审阅业务实现，不能据此发布“全部验收通过”的 1.0.0。
