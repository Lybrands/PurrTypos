---
name: addMemory
description: 当需要向本书长期记忆写入一条要点（设定、人物 note、章节摘要等）时使用。
---

新增记忆条目；非浏览书稿正文或人物全表。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "layer": { "type": "number", "description": "层级：0=全局 1=大纲 2=人物 3=章节" },
    "content": { "type": "string", "description": "记忆内容，简明扼要" },
    "chapterId": { "type": "number", "description": "可选。章节记忆时关联的写作章节 ID" },
    "characterId": { "type": "number", "description": "可选。人物记忆时关联的人物 ID" }
  },
  "required": ["bookId", "layer", "content"]
}
```
