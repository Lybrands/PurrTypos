---
name: getChapterContent
description: 只读：获取写作目录中某一章的纯文本正文。chapterId 须为左侧写作章节目录对应的章节 id（勿用其他大纲树节点 id）；可选 maxTextLength。用于引用现状、续写依据、单章摘要等，不修改书稿。
parameters:
  type: object
  properties:
    chapterId:
      type: number
      description: 写作目录章节 ID（与界面左侧写作章节目录一致）
    title:
      type: string
      description: 章节标题，用于展示
    maxTextLength:
      type: number
      description: 纯文本最大长度，默认 12000
  required:
    - chapterId
---

chapterId 必须对应左侧写作章节目录，不可用其他大纲树中的节点 id。
