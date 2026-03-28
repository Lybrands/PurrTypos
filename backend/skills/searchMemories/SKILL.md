---
name: searchMemories
description: 当需要按关键词检索本书已存的长期记忆或伏笔条目时使用。
---

检索已写入的记忆与伏笔条目，替代不了小说背景全文。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "query": { "type": "string", "description": "检索关键词；不传则返回近期记忆" },
    "layer": { "type": "string", "description": "可选。限定层级：全局 / 大纲 / 人物 / 章节 / 伏笔" },
    "chapterId": { "type": "number", "description": "可选。当前章节 ID，检索章节记忆时可优先本章" },
    "limit": { "type": "number", "description": "可选。最多返回条数，默认 15" }
  },
  "required": ["bookId"]
}
```
