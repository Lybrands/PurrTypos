---
name: editChapterContent
description: 当需要写入写作目录中某章正文时使用[关键词：编写、写入、写进、改写、保存]。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

写操作；与 getChapterContent 成对。**仅允许 chapterId，不支持 chapterTitle/chapterIndex。**

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "chapterId": { "type": "string", "description": "可选。编辑当前章时省略，或传精确别名“当前章节”/“当前章”/“本章”，由宿主绑定；编辑非当前章时只传 listWritingChapters 返回的真实 id，不要传章节标题" },
    "content": { "type": "string", "description": "章节新正文，纯文本，段落之间用换行符分隔" }
  },
  "required": ["content"]
}
```
