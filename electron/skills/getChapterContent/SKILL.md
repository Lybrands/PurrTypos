---
name: getChapterContent
description: 当需要只读读取写作目录中某一章正文（引用原文、续写依据、单章摘要等）时使用。依赖 listWritingChapters（在需要写作目录 chapterId 时）。
---

chapterId 必须对应左侧写作章节目录，不可用其他大纲树中的节点 id。

**优先使用 `chapterTitle`（与附录中章节标题完全一致）或 `chapterIndex`（附录中的 [序号]），不要手写 id。** 分卷模式下附录与序号**仅含可写正文的章节**，不含「卷」行，避免误把卷当作章节读正文。也可省略章节参数，由宿主注入当前界面章节。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "chapterId": { "type": "string", "description": "写作目录章节 id（一般不必填，由宿主或 chapterTitle/chapterIndex 解析）" },
    "chapterTitle": { "type": "string", "description": "章节标题，与附录目录一致时可代替 chapterId" },
    "chapterIndex": { "type": "number", "description": "附录写作章节目录中的 [序号]，1 起" },
    "title": { "type": "string", "description": "章节标题，用于展示" },
    "maxTextLength": { "type": "number", "description": "纯文本最大长度，默认 12000" }
  }
}
```
