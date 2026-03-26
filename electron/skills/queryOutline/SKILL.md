---
name: queryOutline
description: 当需要只读查看本书某条大纲的结构与文本内容（含章节树、提纲）时使用。依赖 listOutlines（在需要 outlineId 列表时）。
---

可使用 **outlineTitle**（与附录中大纲标题一致）或 **outlineIndex**（附录中的 [序号]）代替 outlineIds，勿手写 id。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（可由宿主注入）" },
    "outlineIds": {
      "type": "array",
      "items": { "type": "string" },
      "description": "可选。仅查询这些大纲 id"
    },
    "outlineId": { "type": "string", "description": "单条大纲 id（与 outlineIds 二选一）" },
    "outlineTitle": { "type": "string", "description": "大纲标题，与附录一致时可代替 outlineIds" },
    "outlineIndex": { "type": "number", "description": "附录大纲列表中的 [序号]，1 起" },
    "includeChapters": { "type": "boolean", "description": "可选。是否返回章节树文本，默认 true" },
    "includeText": { "type": "boolean", "description": "可选。是否返回文本大纲 Markdown，默认 true" },
    "maxTextLength": { "type": "number", "description": "可选。文本大纲最大长度，默认 32000" }
  },
  "required": ["bookId"]
}
```
