---
name: getChapterContent
description: 只读：获取写作大纲中某一章的纯文本正文。必须先调用 getWritingOutlineWithChapters(bookId) 取得 chapters[].id 再传入 chapterId；可选 maxTextLength。用于引用现状、续写依据、单章摘要等，不修改书稿。
parameters:
  type: object
  properties:
    chapterId:
      type: number
      description: 写作大纲中的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）
    title:
      type: string
      description: 章节标题，用于展示
    maxTextLength:
      type: number
      description: 纯文本最大长度，默认 12000
  required:
    - chapterId
---

chapterId 必须来自写作大纲，不可用其他大纲的章节 id。
