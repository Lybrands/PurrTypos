---
name: listOutlines
description: 只读获取本书大纲列表（id、title、type），包含总纲。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---
