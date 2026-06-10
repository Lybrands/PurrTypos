---
name: getBookStyle
description: 当需要查看本书「风格基调」(视角/语调/节奏/禁忌/参考章节/作者备注) 时使用。该结构由用户在导演笔记本中配置，是写作时必须遵守的硬约束。
---

返回当前书籍的风格基调字段：pov、tone、pace、banned_rules、reference_chapter_ids（JSON 数组）、free_notes。
若用户未配置，返回 `(暂无风格基调)`。

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
