# 剧本审阅交互恢复设计

日期：2026-08-11

## 目标

恢复“审阅完整剧本”阶段的三个用户入口：业务弹窗能够可靠打开，审阅项可以单条或批量裁决，CURRENT TASK 的快捷操作可以立即向 Agent 发送简短请求。同时不再向用户展示内部审阅 Revision ID。

## 已确认问题

1. `PurrModal` 在 `destroyOnHidden` 且关闭时直接返回 `null`，连 Base UI Dialog Root 一并卸载。实机中，审阅弹窗、项目文档弹窗和确认弹窗都会在点击后保持关闭；普通非弹窗状态按钮正常。
2. 审阅裁决阶段通过 `reviewUsesAgentAction` 隐藏了阶段主操作，只保留打开审阅面板的 `reviewEntry`，因此 CURRENT TASK 不再提供 Agent 快捷发送。
3. 审阅面板标题区通过 `review.reviewRevisionId.slice(-8)` 暴露了内部 Revision ID。

持久化状态排除了“残留运行任务导致按钮禁用”的假设：当前项目最近的 Turn 均为 `completed`，Operation 均为 `succeeded`。

## 采用方案

### 共享弹窗生命周期

`PurrModal` 始终保留 `PurrDialog` / Base UI Dialog Root。关闭时由 Base UI Portal 的默认行为卸载弹层内容，不再由兼容层提前返回 `null`。

保留 `destroyOnHidden` 兼容属性，避免修改现有调用方；它不再控制 Dialog Root 的存在。当前 Base UI Portal 默认不会在关闭时保留内容，因此现有调用方要求的“隐藏后销毁内容”仍成立。

该修复位于共享组件边界，不在每个业务按钮上增加延迟、强制点击或重复状态逻辑。

### CURRENT TASK 双入口

审阅阶段存在待处理意见时，同时显示两个语义不同的操作：

- 主操作“处理审阅意见”：调用现有 `runAgent(promptOverride)`，立即向 Agent 发送简短请求 `处理审阅意见`，不先填入输入框，也不恢复长模板文案。
- 次操作“查看审阅项（N）”：打开“审阅与定稿”弹窗，`N` 为当前待处理数量。

其他审阅子阶段保持现有含义：等待审阅、重新审阅和开始修订继续使用 Agent 主操作；等待定稿和已完成阶段不新增 Agent 请求。

运行中、提交中、项目归档或存在待应用候选稿时，Agent 主操作继续遵守现有禁用规则。查看审阅项只受审阅裁决请求和只读状态约束，不与 Agent 快捷发送混为同一动作。

### 审阅与定稿弹窗

弹窗标题区保留 Agent 建议，并以“当前审阅版本”表示版本状态；不显示完整、截断或散列后的 `reviewRevisionId`。

审阅意见自身的描述、严重程度、涉及场景和处理状态继续显示。单条“待处理”和“批量处理”沿用现有裁决流程：选择目标状态、填写可选说明、确认后调用现有审阅裁决接口。

共享弹窗修复后先验证现有 `PurrDropdown`。只有当实机仍能复现下拉无法打开时，才在 `PurrDropdown` 的弹层归属处修复，不重写整套裁决界面。

## 数据流

```text
CURRENT TASK / 处理审阅意见
  -> runAgent('处理审阅意见')
  -> 现有发送或排队通道

CURRENT TASK / 查看审阅项（N）
  -> reviewAdjudicationOpen = true
  -> PurrModal 保持 Dialog Root
  -> ReviewAdjudicationPanel
  -> 单条或批量选择裁决
  -> adjudicateScreenplayV2Review
  -> 刷新权威 workspace
```

## 错误与并发处理

- 审阅裁决继续以服务端 workspace、项目 Revision 和 review Revision 为权威；过期请求刷新工作区并显示现有错误提示。
- 裁决请求进行中时禁止重复提交，但不把已经结束的 Agent 运行误认为裁决请求。
- Agent 快捷发送沿用现有发送、排队、暂停和候选稿门禁，不新增第二套请求状态。
- 弹窗关闭、页面刷新或项目切换不改变审阅裁决数据。

## 测试与验收

1. 回归测试证明待处理审阅阶段同时产生 Agent 主操作和“查看审阅项”入口，且 Agent 请求正文是简短的 `处理审阅意见`。
2. 实机点击“查看审阅项（N）”后，出现“审阅与定稿”弹窗。
3. 点击单条“待处理”可以选择裁决状态并打开确认界面。
4. 勾选审阅项后点击“批量处理”可以选择批量状态。
5. 弹窗只显示“当前审阅版本”，页面中不再出现审阅 Revision ID 的后缀。
6. 项目文档等其他使用 `destroyOnHidden` 的业务弹窗仍能正常打开和关闭。
7. 运行前端单测、TypeScript 检查、剧本验收与完整 Agent 门禁；实机验证不提交审阅裁决，不修改用户业务数据。

## 非目标

- 不改变审阅裁决的数据库结构、状态机或服务端接口。
- 不让 Agent 代替用户写入 `dismissed`、`riskAccepted` 等人工裁决。
- 不恢复此前删除的长固定模板文案。
- 不重新设计审阅报告内容或定稿门禁。
