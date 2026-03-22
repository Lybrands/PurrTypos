---
name: addForeshadowing
description: 写入：添加一条伏笔记录（悬念、道具、线索、对话等），并标明埋入章节。chapterId 须为写作大纲章节 id（来自 getWritingOutlineWithChapters）。仅在用户明确要「记录/埋下伏笔」时使用；若用户只要看小说背景或总结人物，应使用 getStoryBackground、getBookCharacters 等只读工具，不要用本工具。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    chapterId:
      type: number
      description: 埋入伏笔的章节 ID（来自 getWritingOutlineWithChapters 的 chapters[].id）
    content:
      type: string
      description: 伏笔内容，简明扼要
    type:
      type: string
      description: "可选。类型：悬念 / 道具 / 线索 / 对话，默认 悬念"
  required:
    - bookId
    - chapterId
    - content
---

写伏笔库；与「读背景/读人物」类意图区分。
