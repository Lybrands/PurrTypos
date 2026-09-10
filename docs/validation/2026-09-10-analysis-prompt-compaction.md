# 来源片段分析提示词整理

状态：代码完成；142 项确定性后端测试通过，未做真实 Provider 对照或 Electron 桌面验收。

## 最终行为

提取指令按资料分类、证据、写法候选、保存顺序组织。字段层级、必填字段、引文长度与数组上限沿用现有工具 schema，系统文本不重复罗列；未修改来源验证、持久化协议或执行轮数。

- 保留实体与时间状态区分、背景 Markdown、原作与未来创作边界、Obsidian 引用语法。
- 明确章节来源只适用于指定综合类型的 summary/inference；关键事实和写法候选仍需精确引文。
- 明确 observations 与 craftCards 使用同一条目结构；批量保存后的最终空数组不会清空已保存内容。
- 移除通用“完整替换”措辞，避免与提取、归一阶段的宿主合并行为冲突；概览和技法提交仍由各自 schema 与 submissionContract 约束。
- 冻结输入已按需提供 observationAccess，通用指令不再重复解释 observationCount。来源原文仅作证据的安全边界保留在通用指令中。
- 修正 sourceSpanId 描述，允许直接使用 sourceEvidence.excerpts 中的编号，不暗示必须先搜索。

## 输入量

比较提取阶段实际拼装的系统消息（提取指令＋通用后缀，不含失败重试附加提示），按 Python `len()` 统计 Unicode 字符：

| 项目 | 整理前 | 整理后 |
| --- | ---: | ---: |
| 单次系统消息 | 3,084 | 1,675 |
| 重复携带 8 轮的累计文本 | 24,672 | 13,400 |

该消息减少 45.7%，8 轮少携带 11,272 字符。这里不包含工具 schema、其他宿主提示、来源正文和历史消息；字符数不是模型 token 或费用，不能据此宣称总输入或账单同比降低。

## 验证

执行 `backend/tests/test_novel_analysis.py`、`test_novel_analysis_conversation.py`、`test_writing_technique_generation.py`、`test_analysis_provenance.py`、`test_analysis_evidence_references.py`：142 项通过。

覆盖既有批次合并、最终提交、关键事实不能降级为章节来源、精确片段引用、来源隔离、阶段工具及确定性模型替身执行。未新增逐字锁定文案的测试，也没有真实模型质量改善结论。

未重启正在使用的后端；重启后，新执行使用整理后的提示词。
