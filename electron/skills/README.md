# Agent 工具目录（PurrTypos）

## 合并策略 A

| 来源 | 用途 |
|------|------|
| `electron/agentToolDefinitions.js` | **主对话模型**收到的 `tools`：`name`、`description`、`parameters` |
| `electron/toolExecutor.js` | 工具实际执行 |
| 本目录各 `<toolName>/SKILL.md` | **仅路由层**：frontmatter + 正文摘要用于 **embedding** 与 **Ollama 意图**；**不**改变主模型 tools |

**向量检索用 query 文本**（主进程）由 `electron/toolRouterQueryText.js` 的 `buildToolRouterEmbeddingQuery(messages)` 生成：固定为 **【对话历史】** + 最近 **3 轮**（每轮用户 + 如有则助手）+ **【当前提问】**，再交给 `toolRouter` 做 query 向量（与意图识别共用该段文本）。轮数常量：`VECTOR_HISTORY_ROUNDS`（当前为 3）。控制台会打印 `[toolRouter][步骤1-向量匹配] 检索用文本（向量模型输入，全文）`。

## 目录约定（与常见 Skills 布局对齐）

```
skills/
├── README.md                 # 本说明
├── skills.md                 # 可选：人工总览
└── <toolName>/               # 与 function.name 一致
    └── SKILL.md              # 可选；无则路由回退到 JS 的 description
```

可选扩展（当前未强制）：`references/` 长文档——若将来要做「仅路由层读取」，再接入 `toolRouter`。

## SKILL.md 建议

- Frontmatter：`name`（与目录名一致）、`description`（触发/语义，供路由；兼容旧档可仍写 `short_description`）。
- 正文：给人看的详细说明；路由会取一段纯文本摘要参与 embed，**不会**整段进入主模型 tools。
