---
name: searchMemories
description: 只读：按关键词检索本书长期记忆（含全局/大纲/人物/章节/伏笔等层），用于回忆设定、伏笔回收、前后一致。可限定 layer。注意：「小说背景」成文档的正文优先用 getStoryBackground；本工具偏碎片化已存记忆条目。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    query:
      type: string
      description: 检索关键词；不传则返回近期记忆
    layer:
      type: string
      description: 可选。限定层级：全局 / 大纲 / 人物 / 章节 / 伏笔
    chapterId:
      type: number
      description: 可选。当前章节 ID，检索章节记忆时可优先本章
    limit:
      type: number
      description: 可选。最多返回条数，默认 15
  required:
    - bookId
---

检索已写入的记忆与伏笔条目，替代不了小说背景全文。
