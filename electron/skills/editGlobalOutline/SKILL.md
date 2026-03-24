---
name: editGlobalOutline
description: 编辑本书的总纲文本；仅当用户提到修改、编辑、改写总纲的时候匹配。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID（用于归属校验）
    markdownContent:
      type: string
      description: 总纲 Markdown 全文（覆盖写入）
  required:
    - bookId
    - markdownContent
---
