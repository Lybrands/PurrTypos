# Agent 工具目录（PurrTypos）

## 装载与执行链路

| 模块 | 职责 |
|------|------|
| `backend/services/tool_router.py` | **扫描**本目录下各 `<toolName>/SKILL.md`，产出 skill 条目（`get_api_skill_items`）；**不做任何路由**——历史上的 embedding Top-K 检索 / 本地模型意图分类均已废弃 |
| `backend/services/agent_tool_definitions.py` | 把 skill 条目转成 OpenAI function-calling 工具定义（`to_openai_tools`） |
| `backend/services/tool_executor.py` + `tool_handlers/` | 工具实际执行（handler 注册名须与目录名一致） |
| 本目录各 `<toolName>/SKILL.md` | **唯一真源**：frontmatter 仅 `name`、`description`；正文含 **\`\`\`json** 的 parameters schema |

**工具选择由主模型完成**：`/ai/chat/stream` 在 agent 模式下把全部工具 schema 一次性发给大模型，
由模型基于 `description` + parameters 自行决定调用哪些工具。不依赖本地模型、不需要 Ollama。

## 目录约定（与常见 Skills 布局对齐）

人工总览（与实现若有出入以各 `SKILL.md` 为准）：[`skills.md`](skills.md)。

```
skills/
├── README.md                 # 本说明
├── skills.md                 # 可选：人工总览
└── <toolName>/               # 与 function.name 一致
    └── SKILL.md              # 工具定义唯一真源
```

## SKILL.md 建议

- **Frontmatter 仅**：`name`（与目录名一致）、`description`（使用场景，可含「依赖某某 skill」等短句）。**不要**写 `parameters` 及其他字段。
- **正文**：人读说明；并**必须**含至少一个 **\`\`\`json** 代码块，内容为发给大模型的 **parameters JSON Schema**。
- 兼容：`short_description` 可作为 `description` 别名。
