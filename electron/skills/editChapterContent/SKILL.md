---
name: editChapterContent
description: 写入：将新正文（纯文本，段落用换行符）保存到写作大纲中的指定章。chapterId 必须来自 getWritingOutlineWithChapters(bookId) 的 chapters[].id。用于按用户要求改写、替换整章、润色扩写等会修改书稿的操作。
parameters:
  type: object
  properties:
    chapterId:
      type: number
      description: 写作大纲中的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）
    content:
      type: string
      description: 章节新正文，纯文本，段落之间用换行符分隔
  required:
    - chapterId
    - content
---

写操作；与 getChapterContent 成对，id 规则相同。
