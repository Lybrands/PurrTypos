---
name: batchGetOutlineDetails
description: 只读：按大纲 ID 列表获取每个大纲的详情（子章节树与层级文本）。须已知 outlineIds（来自 getAvailableOutlines 或 getAllOutlines）。用于精读特定几条大纲的结构与内容，而非只列标题。
parameters:
  type: object
  properties:
    outlineIds:
      type: array
      description: 大纲 ID 列表
    bookId:
      type: number
      description: 当前书籍 ID，用于解析 allOutlines
  required:
    - outlineIds
    - bookId
---

先有可关联大纲 id 再拉详情。
