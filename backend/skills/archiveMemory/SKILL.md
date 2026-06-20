---
name: archiveMemory
description: 当某条长期记忆已经过时、不应默认召回，但仍需要保留历史时使用。
---

归档长期记忆。归档后默认不会进入自动召回。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "number", "description": "长期记忆 ID；先用 searchMemories 获取" }
  },
  "required": ["id"]
}
```
