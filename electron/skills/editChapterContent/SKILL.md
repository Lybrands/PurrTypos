---
name: editChapterContent
description: 写入：将新正文（纯文本，段落用换行符）保存到写作目录中的指定章。chapterId 须为左侧写作章节目录对应的章节 id（勿用其他大纲树节点 id）。用于按用户要求改写、替换整章、润色扩写等会修改书稿的操作。
parameters:
  type: object
  properties:
    chapterId:
      type: number
      description: 写作目录章节 ID（与界面左侧写作章节目录一致）
    content:
      type: string
      description: 章节新正文，纯文本，段落之间用换行符分隔
  required:
    - chapterId
    - content
---

写操作；与 getChapterContent 成对，id 规则相同。
