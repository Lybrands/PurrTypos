---
name: getWritingOutlineWithChapters
description: 获取本书「写作大纲」及左侧写作目录中的章节列表。返回的 chapters[].id 是读取/编辑章节正文时唯一合法 ID；其他大纲（总纲、章节大纲等）里的章节 id 不能用于 getChapterContent 或 editChapterContent，否则会取不到正文。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

取正文前必须先由此拿到写作大纲中的章节 id。
