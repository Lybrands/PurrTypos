---
name: createWritingChapter
description: 当需要在写作目录中新建章节或卷内子章节时使用[关键词：创建章节、新增章节、插入章节、新建小节]。依赖 listWritingChapters（在需要 parentId 时）。
---

写操作。优先创建"章节"而不是"卷"：

- 章节标题由系统自动生成，格式为"第n章"（n 从同级现有章节自动递增），**无需传 `title`**。
- 传 `parentId` 时：在该父节点下创建子章节。
- 未传 `parentId` 时：
  - 若当前会话章节位于某一卷下，则默认在**当前卷**下创建同级章节。
  - 若当前是平铺目录（无卷），则在写作目录根级创建章节。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "parentId": { "type": "string", "description": "可选，父章节 ID；用于在卷下创建子章节" }
  },
  "required": ["bookId"]
}
```
