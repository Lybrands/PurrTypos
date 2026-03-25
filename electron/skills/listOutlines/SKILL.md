---
name: listOutlines
description: 当需要本书大纲条目列表（id、标题、类型，含总纲）时使用。
---

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" }
  },
  "required": ["bookId"]
}
```
