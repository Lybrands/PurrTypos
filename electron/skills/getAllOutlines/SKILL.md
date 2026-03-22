---
name: getAllOutlines
description: 获取本书全部大纲类型（总纲、卷大纲、章节大纲、其他大纲、写作大纲）及每类下的子章节树与层级文本。用于查看全书结构、对比不同大纲类型，或需要完整大纲全景而非仅写作目录时。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

含五类大纲及子树；与仅取写作目录的 getWritingOutlineWithChapters 不同。
