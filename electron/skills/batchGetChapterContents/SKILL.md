---
name: batchGetChapterContents
description: 只读：批量获取多章正文（纯文本）。每个 chapterId 均须来自写作大纲 chapters[].id。适合跨章对比、连续多章摘要、精读若干章而无需逐次调用 getChapterContent。
parameters:
  type: object
  properties:
    chapterIds:
      type: array
      description: 章节 ID 列表
    maxTextLength:
      type: number
      description: 每章纯文本最大长度，默认 12000
  required:
    - chapterIds
---

多章只读；id 来源与 getChapterContent 相同。
