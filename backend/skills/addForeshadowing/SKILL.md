---
name: addForeshadowing
description: 当需要写入一条伏笔记录并关联埋入章节时使用。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

写伏笔库；与「读背景/读人物」类意图区分。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "chapterId": { "type": "number", "description": "埋入伏笔的写作目录章节 ID（左侧写作章节目录）" },
    "content": { "type": "string", "description": "伏笔内容，简明扼要" },
    "type": { "type": "string", "description": "可选。类型：悬念 / 道具 / 线索 / 对话，默认 悬念" }
  },
  "required": ["bookId", "chapterId", "content"]
}
```
