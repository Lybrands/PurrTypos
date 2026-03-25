---
name: listWritingChapters
description: 当需要本书写作目录的卷/章 id 与标题或枚举可写章节时使用。
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
