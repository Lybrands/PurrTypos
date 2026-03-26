# Agent 工具目录（PurrTypos）

## 合并策略 A

| 来源 | 用途 |
|------|------|
| `electron/agentToolDefinitions.js` | 仅 **`SKILL_SPECS`**（编排 DAG）与 **`toOpenAiTools`**；**不再**维护重复的工具清单 |
| `electron/toolRouter.js` | **扫描**本目录下各 `<toolName>/SKILL.md`，作为发给大模型的 `tools`（与 `getToolsForQuery` / `getApiSkillItems` 同源） |
| `electron/toolExecutor.js` | 工具实际执行（`name` 须与目录名一致） |
| 本目录各 `<toolName>/SKILL.md` | **唯一真源**：frontmatter 仅 `name`、`description`；正文含 **\`\`\`json** 的 parameters schema；正文摘要参与 **embedding** |

**向量检索用 query 文本**（主进程）由 `electron/toolRouterQueryText.js` 的 `buildToolRouterEmbeddingQuery(messages)` 生成：固定为 **【对话历史】** + 最近 **3 轮**（每轮用户 + 如有则助手）+ **【当前提问】**，再交给 `toolRouter` 做 query 向量（与意图识别共用该段文本）。轮数常量：`VECTOR_HISTORY_ROUNDS`（当前为 3）。控制台会打印 `[toolRouter][步骤1-向量匹配] 检索用文本（向量模型输入，全文）`。

## 目录约定（与常见 Skills 布局对齐）

人工总览（与实现若有出入以各 `SKILL.md` 为准）：[`skills.md`](skills.md)。

```
skills/
├── README.md                 # 本说明
├── skills.md                 # 可选：人工总览
└── <toolName>/               # 与 function.name 一致
    └── SKILL.md              # 可选；无则路由回退到 JS 的 description
```

可选扩展（当前未强制）：`references/` 长文档——若将来要做「仅路由层读取」，再接入 `toolRouter`。

## SKILL.md 建议

- **Frontmatter 仅**：`name`（与目录名一致）、`description`（使用场景，可含「依赖某某 skill」等短句）。**不要**写 `parameters` 及其他字段。
- **正文**：人读说明；并**必须**含至少一个 **\`\`\`json** 代码块，内容为发给大模型的 **parameters JSON Schema**。路由嵌入时用 `description` + 去掉 JSON 块后的正文摘要。
- 兼容：`short_description` 可作为 `description` 别名。
