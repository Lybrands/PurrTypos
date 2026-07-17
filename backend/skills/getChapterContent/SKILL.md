---
name: getChapterContent
description: 当需要只读读取写作目录中某一章正文（引用原文、续写依据、单章摘要等）时使用。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

chapterId 必须对应左侧写作章节目录，不可用思维导图大纲树中的节点 id。

**仅允许使用 `chapterId`。** 不支持 `chapterTitle`、`chapterIndex`。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "chapterId": { "type": "string", "description": "可选。读取当前章时省略，或传精确别名“当前章节”/“当前章”/“本章”，由宿主绑定；读取非当前章时只传 listWritingChapters 返回的真实 id，不要传章节标题" },
    "title": { "type": "string", "description": "章节标题，用于展示" },
    "maxTextLength": { "type": "number", "description": "纯文本最大长度，默认 12000" }
  }
}
```
