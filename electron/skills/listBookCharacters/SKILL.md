---
name: listBookCharacters
description: 只读：返回 JSON 字符串，数组项为 {"id","name"}，无详细人设。先拿 id 再 getBookCharacters(characterIds) 或 addMemory(characterId)。空为 []。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

示例：`[{"id":3,"name":"张三"},{"id":7,"name":"李四"}]`。与 getBookCharacters 互补。
