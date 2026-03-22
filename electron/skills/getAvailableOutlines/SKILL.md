---
name: getAvailableOutlines
description: 只读：获取本书可关联到对话的大纲列表（总纲、章节大纲、其他大纲，扁平 id 与标题）。用于挑选要带入上下文的大纲，或作为 batchGetOutlineDetails 的 outlineIds 来源。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

扁平列表；不含写作大纲全书树（那是 getWritingOutlineWithChapters / getAllOutlines）。
