---
name: batchGetChapterContents
description: 当同一轮需要读取多章写作正文（对比、连载摘要、精读等）时使用。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

多章只读；id 来源与 getChapterContent 相同。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "chapterIds": {
      "type": "array",
      "items": { "type": "string" },
      "description": "章节 ID 列表（仅允许 listWritingChapters.items[].id）"
    },
    "maxTextLength": { "type": "number", "description": "每章纯文本最大长度，默认 12000" }
  },
  "required": ["chapterIds"]
}
```
