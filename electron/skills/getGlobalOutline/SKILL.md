---
name: getGlobalOutline
description: 获取总纲文本内容；仅当用户说明总纲的时候需要匹配。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    maxTextLength:
      type: number
      description: 可选。总纲 Markdown 最大长度，默认 32000
  required:
    - bookId
---
