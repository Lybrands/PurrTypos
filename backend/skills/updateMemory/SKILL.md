---
name: updateMemory
description: 当需要修改一条已存在的长期记忆内容、重要性、scope 或固定状态时使用。
---

更新长期记忆。先用 searchMemories 获取 id 和 version，不要猜测；状态变更使用专门的启用或归档操作。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "string", "description": "长期记忆 ID" },
    "version": { "type": "integer", "minimum": 1, "description": "searchMemories 返回的当前版本" },
    "kind": { "type": "string", "enum": ["canon", "plot", "character", "world", "foreshadowing", "style", "summary"] },
    "content": { "type": "string", "description": "新的记忆正文" },
    "summary": { "type": "string", "description": "新的摘要" },
    "keywords": { "type": "string", "description": "新的关键词" },
    "importance": { "type": "number", "description": "1-5" },
    "confidence": { "type": "number", "description": "0-1" },
    "pinned": { "type": "boolean" },
    "scopeType": { "type": "string", "enum": ["book", "chapter", "character", "outline"] },
    "scopeId": { "type": "string", "description": "scope 对应的 ID" }
  },
  "required": ["id", "version"]
}
```
