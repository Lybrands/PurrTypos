---
name: linkMemories
description: 当需要标记两条长期记忆之间的覆盖、冲突、佐证或弱关联关系时使用。
---

关联长期记忆。覆盖或冲突关系不会自动删除内容，用户仍可在记忆中心审阅。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前书籍 ID；宿主通常会自动注入" },
    "fromMemoryId": { "type": "number", "description": "发起关系的记忆 ID" },
    "toMemoryId": { "type": "number", "description": "目标记忆 ID" },
    "relation": { "type": "string", "enum": ["supersedes", "contradicts", "supports", "relates_to"], "description": "关系类型" },
    "note": { "type": "string", "description": "可选。关系说明" }
  },
  "required": ["bookId", "fromMemoryId", "toMemoryId", "relation"]
}
```
