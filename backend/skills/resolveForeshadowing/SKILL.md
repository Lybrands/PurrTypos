---
name: resolveForeshadowing
description: 当用户确认某条伏笔已经在当前或指定章节回收时使用。
---

把伏笔状态改为“已回收”。先用 searchSparkIdeas 找到伏笔 ID，不要猜测 ID；需要处理通用长期记忆时，再使用 searchMemories 和对应的记忆操作。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "number", "description": "伏笔 ID" },
    "resolvedChapterId": { "type": "string", "description": "可选。回收伏笔的章节 ID；不传时使用当前章节" }
  },
  "required": ["id"]
}
```
