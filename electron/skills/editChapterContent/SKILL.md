---
name: editChapterContent
description: 当需要写入写作目录中某章正文时使用[关键词：编写、写入、写进、改写、保存]。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

写操作；与 getChapterContent 成对。**优先 chapterTitle 或 chapterIndex，勿手写 id。**

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "chapterId": { "type": "string", "description": "写作目录章节 id（可由 chapterTitle/chapterIndex 解析）" },
    "chapterTitle": { "type": "string", "description": "章节标题，与附录目录一致" },
    "chapterIndex": { "type": "number", "description": "附录写作目录 [序号]，1 起" },
    "content": { "type": "string", "description": "章节新正文，纯文本，段落之间用换行符分隔" }
  },
  "required": ["content"]
}
```
