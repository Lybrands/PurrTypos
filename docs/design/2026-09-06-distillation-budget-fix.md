# 蒸馏输出预算耗尽：修复与验证

本文记录公共入口重构前的历史验证。后续已经恢复同章蒸馏、修复证据范围和提交契约，并生成待审核 Skill；Root 总结另被框架推理模式遗漏阻断。最新结论见[后续验收](2026-09-06-agent-and-distillation-acceptance.md)。

## 定位

此前同一章的提取单元在每次 10,000 Token 的限额下失败，单次调用的思考用量分别为 9,972 和 9,982。该现象只证明输出预算耗尽，不能单凭此断定模型故障。

代码核对发现：`zai_chat._build_chat_params` 未将 `reasoning_effort` 转交 SDK，GLM 前端预设也未声明支持的推理强度。因此显式配置可能在适配器丢失，而无配置时使用 Provider 的默认强度。

[智谱官方深度思考文档](https://docs.bigmodel.cn/cn/guide/capabilities/thinking)与[官方模型卡](https://huggingface.co/zai-org/GLM-5.3-Flash-BF16)说明：GLM-5.3-Flash 不能关闭思考，支持 low/high/max，缺省为 max。降低生成上限不等于降低思考强度。

## 改动

- 智谱流式与非流式请求均透传 `reasoning_effort`；GLM 前端预设开放 low/high/max。
- 当时小说分析在未显式指定 GLM 推理强度时采用 low。此规则已由公共入口的三态配置取代：只有 Inherit 可采用任务偏好；ProviderDefault 不覆盖，Explicit 原样传递。详见[模型请求规范](2026-09-06-model-request-boundary-refactor.md)。继续保持思考开启和原有生成上限。
- 参考剧本 Agent 按单元限制工具的方式，为蒸馏、试写、修订、复评注册各自提交工具与 schema；每轮只启用读取工具及本阶段的提交工具。移除一个提交工具同时暴露全部阶段 anyOf 的设计。
- 工具权限、原文读取、候选持久化、证据验证、公开流式说明和阶段依赖仍由既有边界处理。

剧本 Agent 的显式推理强度传递与按任务选择工具是可复用做法，并不是“剧本已经在相同 GLM 配置下通过预算问题验收”的证据。

## 验证边界

真实测试使用新进程加载当前代码，在临时数据库运行同一个 2,225 字符章节、同一 GLM-5.3-Flash 与 10,000 Token 单次上限。正式来源、书籍和已发布方法不变。

本次临时目录：`/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-distill-fixed-fkqc6oi7`。
Root Run：`run_52056c12123d4279`；提取 Run：`run_07bc4590552048f6`。

提取首次尝试成功，整个提取 Run 的生成用量为 2,938，其中思考为 19；这是 Run 内调用合计，不应与旧记录中单次调用的用量直接混同。新调用收据记录 reasoning_effort=low，maxGenerationTokens=10000。

确定性检查：后端 2,100 项通过，前端全套、类型检查及 Web 构建通过，补充 GLM 前端参数测试通过。SDK 参数透传覆盖流式/非流式及三档强度；阶段工具隔离、显式配置不被覆盖、候选提交与公开日志均有回归。

提取成功证明本样本上的预算阻断已解除，不等于成熟 Skill 或普遍质量已经通过。完整蒸馏与试写复评结果需另外记录。

## 完整流程实际结果

本次临时任务最终失败于 aggregate:story 的模型后置校验，错误为 `novel analysis evidence lost its segment scope`。归一 Run `run_144c57ae75c54cc0` 使用 3,365 个生成 Token（思考 30）；聚合 Run `run_de1151c295a04f66` 使用 3,498 个生成 Token（思考 30）。两者模型调用均已完成；聚合候选也包含 storyOverview，但引用不能按 sectionId + 原始 excerpt 还原到上游片段范围。已对真实持久化候选离线重放该校验，复现同一错误。

因此本次消除了该样本的输出预算阻断，没有证明完整蒸馏成功。后续 skill、试写、修订及复评单元均因依赖失败取消；没有最终 skill。未放宽证据约束。临时测试进程正常退出，正式数据未改动。
