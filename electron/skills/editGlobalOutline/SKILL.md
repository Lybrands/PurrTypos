---
name: editGlobalOutline
description: 当需要修改或保存本书总纲（Markdown）时使用。
---

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（用于归属校验）" },
    "markdownContent": { "type": "string", "description": "总纲 Markdown 全文（覆盖写入）" }
  },
  "required": ["bookId", "markdownContent"]
}
```
