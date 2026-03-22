---
name: getBookContext
description: 一次性获取当前书籍的完整写作上下文：当前章节正文、写作大纲、小说背景正文、全部人物卡片。适合开场全面了解本书，或用户同时问到「写到哪、大纲、设定、角色」时调用，可减少多次单独查询。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID
    currentChapterId:
      type: number
      description: 当前正在写的章节 ID，不传则只取大纲/背景/人物
    currentChapterTitle:
      type: string
      description: 当前章节标题，用于展示
  required:
    - bookId
---

聚合当前章、写作大纲、小说背景与人物，适合需要一书全貌时。
