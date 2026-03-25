---
name: updateOutline
description: 当需要更新某条大纲的标题、文本提纲、Markdown、XMind 或文件路径时使用。依赖 listOutlines（获取 outlineId）。
---

**优先 outlineTitle 或 outlineIndex（附录大纲列表 [序号]），勿手写 id。**

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前书籍 id（用于归属校验，可由宿主注入）" },
    "outlineId": { "type": "string", "description": "目标大纲 id（可由 outlineTitle/outlineIndex 解析）" },
    "outlineTitle": { "type": "string", "description": "大纲标题，与附录一致" },
    "outlineIndex": { "type": "number", "description": "附录大纲列表 [序号]，1 起" },
    "title": { "type": "string", "description": "可选。更新大纲标题" },
    "xmind_data": { "type": "string", "description": "可选。更新 XMind JSON 文本" },
    "file_path": { "type": "string", "description": "可选。更新源文件路径" },
    "markdown_content": { "type": "string", "description": "可选。更新文本大纲 Markdown" }
  },
  "required": ["bookId"]
}
```
