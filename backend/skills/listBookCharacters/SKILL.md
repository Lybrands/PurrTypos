---
name: listBookCharacters
description: 当只需要本书角色的 id 与姓名列表、不要人设详情时使用。
---

示例：`[{"id":3,"name":"张三"},{"id":7,"name":"李四"}]`。与 getBookCharacters 互补。

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
