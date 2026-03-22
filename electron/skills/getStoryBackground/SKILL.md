---
name: getStoryBackground
description: 只读：获取本书「小说背景」正文（世界观、时代、基调、总设定等）。用户要看、总结或讨论小说背景、世界观、设定总述时使用；读正文不是 searchMemories 的主要场景（后者是碎片化长期记忆）。常与 getBookCharacters 搭配。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
  required:
    - bookId
---

小说背景文档正文；与人物工具组合可回答「背景+角色」类问题。
