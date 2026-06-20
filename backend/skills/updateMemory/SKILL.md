---
name: updateMemory
description: 当需要修改一条已存在的长期记忆内容、状态、重要性、scope 或固定状态时使用。
---

更新长期记忆。先用 searchMemories 获取 `[id:N]`，不要猜测 ID。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "number", "description": "长期记忆 ID" },
    "kind": { "type": "string", "enum": ["canon", "plot", "character", "world", "foreshadowing", "style", "summary"] },
    "content": { "type": "string", "description": "新的记忆正文" },
    "summary": { "type": "string", "description": "新的摘要" },
    "keywords": { "type": "string", "description": "新的关键词" },
    "importance": { "type": "number", "description": "1-5" },
    "confidence": { "type": "number", "description": "0-1" },
    "status": { "type": "string", "enum": ["active", "pending", "archived", "superseded"] },
    "pinned": { "type": "boolean" },
    "scopeType": { "type": "string", "description": "book / chapter / character / outline / session" },
    "scopeId": { "type": "string", "description": "scope 对应的 ID" }
  },
  "required": ["id"]
}
```
