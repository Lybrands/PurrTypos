---
name: resolveForeshadowing
description: 当用户确认某条伏笔已经在当前或指定章节回收时使用。
---

把伏笔状态改为“已回收”，并同步归档对应的长期记忆镜像。先用 searchSparkIdeas 或 searchMemories 找到伏笔 ID，不要猜测 ID。

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
