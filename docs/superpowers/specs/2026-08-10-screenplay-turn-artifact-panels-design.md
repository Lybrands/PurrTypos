# 剧本对话逐轮产物面板与项目文档定位设计

日期：2026-08-10

## 目标

剧本 Agent 每一轮成功生成的 Revision 都属于产生它的那轮对话。无论实时完成、刷新页面、滚动旧轮次还是切换历史对话，产物面板都必须出现在对应 Assistant 消息之后；面板中的查看和应用操作必须绑定该轮 Revision。

从产物面板打开“项目文档”时，弹窗必须直接选择该 Revision 的文档分类和具体版本。普通“打开项目文档”入口继续使用工作区默认分类。

## 当前根因

当前前端把 `agentProposal` 和 `agentRevisionRef` 保存为页面级单例。恢复会话时又把 Task 倒序后只读取最后一个 `resultRevisionId`，因此最多只能重建一张最新产物面板。`afterAssistantMessage` 也只匹配这一个 `proposalTurnId`。

“查看候选稿”入口只切换 `revisionLibraryOpen`，没有传递 Task 已经持久化的 `targetRole` 和 `resultRevisionId`。项目文档弹窗打开后调用 `defaultRole(workspace)`，会选择工作区推算出的默认分类，而不是产生点击操作的文档。

## 数据与所有权

- `ScreenplayAgentTask.resultRevisionId` 是对话轮次与产物之间的持久化关系。
- `ScreenplayAgentTask.targetRole` 是该产物的文档分类。
- Revision 是标题、版本号、产物类型和状态的权威来源。
- 前端不持久化第二份面板 JSON，也不从文案、当前阶段或数组位置猜测归属。

对话快照为每个 Task 增加可空的 `resultRevision` 轻量摘要。应用服务在读取快照时按 `resultRevisionId` 从 Revision 仓储组合该摘要；摘要不包含正文、Parts 或结构化内容。若旧数据只有稳定 Revision 引用而摘要暂时不可用，前端仍按 Task 的 `targetRole` 和 `resultRevisionId` 渲染可操作的基础产物面板。

## 前端投影

新增纯函数把所有带 `resultRevisionId` 的 Task 投影为以 `turnId` 为键的产物描述：

```ts
interface ScreenplayTurnArtifact {
  turnId: string
  taskId: string
  revisionId: string
  role: ScreenplayV2DeliverableRole
  revisionNo: number | null
  title: string
  kind: ScreenplayDocumentKind | null
  status: 'current' | 'historical' | 'candidate'
  sourceRunId: string | null
}
```

投影遍历全部 Task，不只读取最后一个。`AgentConversation.afterAssistantMessage` 根据消息的 `turnId` 读取对应产物，因此历史重放与实时状态使用同一套确定性映射。

面板状态结合最新 Workspace 校正：对应角色的 Head 等于该 Revision 时为当前版本；仍在 `workspace.candidates` 中时为待应用；其余已接受过的摘要保持历史版本状态。面板的按钮回调闭包只接收自己的 `revisionId` 和 `role`，不读取页面级“最新候选稿”。

## 项目文档导航契约

页面维护可空导航目标：

```ts
interface RevisionLibraryTarget {
  role: ScreenplayV2DeliverableRole
  revisionId: string
}
```

- 产物面板调用 `openRevisionLibrary({ role, revisionId })`。
- 普通项目文档入口调用 `openRevisionLibrary(null)`。
- 弹窗收到目标后先选择 `role`，再选择 `revisionId`。
- 目标 Revision 不在第一页历史列表时，仍直接读取其详情并合并进版本选项，不能回退到其他版本。
- 只有目标 Revision 已不存在时才显示明确错误；不能静默打开另一份文档。

## 应用 Revision

应用操作改为 `acceptAgentRevision(artifact)`，直接使用该产物的 Revision ID。它先刷新 Workspace，再判断是否已经是对应角色的 Head，然后调用幂等的 Revision 接受接口。下游失效确认、成功后的 Workspace 同步和文档刷新沿用现有业务规则。

旧的 `saveAgentProposal` 不再作为面板操作前置条件，因为出现 `resultRevisionId` 已经证明候选 Revision 完成持久化。历史面板不得复用最新产物的全局 Apply 回调。

## 加载与降级

- 对话快照携带轻量 Revision 摘要，避免前端为每一轮发起延迟请求以及面板逐张出现造成布局抖动。
- 摘要缺失时显示基于角色的稳定标题和版本未知状态，但查看仍精确使用 Revision ID。
- Revision 详情读取失败时保留当前文档分类和版本选择，不自动切换到默认版本。
- 无 `resultRevisionId` 的回答轮、失败轮和取消轮不显示产物面板。

## 测试要求

### 后端

- 对话快照中的每个已完成 Task 都携带自己的 `resultRevisionId` 和轻量 `resultRevision`。
- 摘要不包含 `parts`、`contentText` 或 `contentJson`。
- Revision 详情返回 `current`、`historical` 或 `candidate` 状态，供精确版本显示和操作使用。

### 前端

- 两个不同 Turn 的 Task 投影为两张分别归属对应 Turn 的产物面板。
- 无产物 Task 不创建面板，摘要缺失仍保留稳定 Revision 定位。
- Workspace 更新后只有真正的 Head 显示为当前版本。
- 产物入口传递自己的 `role + revisionId`；普通入口清空目标。
- 项目文档弹窗优先选择显式目标，并能打开不在首屏历史列表中的目标 Revision。

## 验收标准

1. 连续完成两轮生成任务后，两轮 Assistant 消息后各有自己的产物面板。
2. 刷新页面或切换历史对话后，两张面板位置和内容保持一致。
3. 点击任一历史面板的“查看候选稿”，项目文档直接选择该轮文档分类和 Revision。
4. 点击任一面板的“应用”，只操作该面板对应的 Revision。
5. 普通项目文档入口仍采用默认分类，不继承上一次产物定位。
6. 快照和消息列表不复制完整正文或结构化 JSON，也不因逐条补请求产生明显布局抖动。
