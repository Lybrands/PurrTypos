---
name: queryOutline
description: 只读查询本书大纲。用户问「大纲、提纲、文本大纲、本章大纲、总纲/章节大纲/其他大纲里写了什么」时用。返回 id、标题、类型、可选 chaptersText 与 markdown；可先 listOutlines 再按 outlineIds 查。与写作章正文无关。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    outlineIds:
      type: array
      description: 可选。仅查询这些大纲 ID
    includeChapters:
      type: boolean
      description: 可选。是否返回章节树文本，默认 true
    includeText:
      type: boolean
      description: 可选。是否返回文本大纲 Markdown，默认 true
    maxTextLength:
      type: number
      description: 可选。文本大纲最大长度，默认 32000
  required:
    - bookId
---
