# Agent 工具目录（PurrTypos）

## 装载与执行链路

| 模块 | 职责 |
|------|------|
| `backend/infrastructure/writing/skill_catalog.py` | 扫描本目录下各 `<toolName>/SKILL.md`，生成实例级、不可变的工具声明快照 |
| `backend/domains/writing/tools/catalog.py` | 校验 Schema、Policy、Handler 和缓存探针集合完全一致，并注册为 Core 工具 |
| `backend/domains/writing/policies.py` / `planning.py` | 定义工具风险、审批要求、规划依赖和步骤授权范围 |
| `backend/infrastructure/writing/tools/` | 装配并执行具体 Writing Handler，校验书籍与章节作用域 |
| 本目录各 `<toolName>/SKILL.md` | **唯一真源**：frontmatter 仅 `name`、`description`；正文含 **\`\`\`json** 的 parameters schema |

`Agent Core` 根据 Writing Planner 的当前步骤只暴露获准工具 Schema；模型只能在该范围内选择，
宿主随后再次执行 allowlist、Policy、参数与对象归属校验。不依赖本地路由模型或 Ollama。

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
