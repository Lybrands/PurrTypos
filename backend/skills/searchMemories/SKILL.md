---
name: searchMemories
description: 当需要检索长期记忆池中的设定、剧情事实、人物状态、世界观、伏笔、风格或总结时使用。
---

检索长期记忆。默认只查 active 记忆；不要把 pending 候选当作既成事实。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前书籍 ID；宿主通常会自动注入" },
    "query": { "type": "string", "description": "检索关键词或当前问题" },
    "kinds": {
      "type": "array",
      "items": { "type": "string", "enum": ["canon", "plot", "character", "world", "foreshadowing", "style", "summary"] },
      "description": "可选。限定记忆类型"
    },
    "statuses": {
      "type": "array",
      "items": { "type": "string", "enum": ["active", "pending", "archived", "superseded"] },
      "description": "可选。默认只查 active；pending 只能用于候选审阅"
    },
    "scopeType": { "type": "string", "description": "可选。book / chapter / character / outline / session" },
    "scopeId": { "type": "string", "description": "可选。scope 对应的 ID" },
    "limit": { "type": "number", "description": "可选。最多返回条数，默认 12" }
  },
  "required": ["bookId"]
}
```
