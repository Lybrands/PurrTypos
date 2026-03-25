---
name: getGlobalOutline
description: 当需要只读查看本书总纲全文时使用。
---

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "maxTextLength": { "type": "number", "description": "可选。总纲 Markdown 最大长度，默认 32000" }
  },
  "required": ["bookId"]
}
```
