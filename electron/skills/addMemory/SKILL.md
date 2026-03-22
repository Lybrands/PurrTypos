---
name: addMemory
description: 写入：向本书长期记忆添加一条要点（设定摘要、人物 note、章节要点等）。layer：0=全局 1=大纲 2=人物 3=章节；章节记忆可传 chapterId，人物记忆可传 characterId。后续可由 searchMemories 检索。用户仅想「查看」已有内容时不要调用——应使用 getStoryBackground、getBookCharacters 或 searchMemories 只读查询。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    layer:
      type: number
      description: 层级：0=全局 1=大纲 2=人物 3=章节
    content:
      type: string
      description: 记忆内容，简明扼要
    chapterId:
      type: number
      description: 可选。章节记忆时关联的写作章节 ID
    characterId:
      type: number
      description: 可选。人物记忆时关联的人物 ID
  required:
    - bookId
    - layer
    - content
---

新增记忆条目；非浏览书稿正文或人物全表。
