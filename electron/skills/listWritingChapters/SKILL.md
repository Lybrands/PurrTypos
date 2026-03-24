---
name: listWritingChapters
description: 获取本书写作目录章节列表信息，使用场景：获取某一章节的信息，如id、名称、卷名称、卷id等。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

