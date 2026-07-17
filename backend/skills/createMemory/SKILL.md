---
name: createMemory
description: 当用户明确要求记住某个长期事实、设定、人物状态、风格规则或阶段总结时使用。
---

创建长期记忆。普通脑暴和未确认建议不要直接写入 active；不确定时写 pending 或先询问用户。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前书籍 ID；宿主通常会自动注入" },
    "kind": { "type": "string", "enum": ["canon", "plot", "character", "world", "foreshadowing", "style", "summary"], "description": "记忆类型" },
    "content": { "type": "string", "description": "记忆正文，必须简明且可直接注入 AI 上下文" },
    "summary": { "type": "string", "description": "可选。更短摘要" },
    "keywords": { "type": "string", "description": "可选。空格分隔关键词" },
    "scopeType": { "type": "string", "description": "可选。book / chapter / character / outline / session，默认 book" },
    "scopeId": { "type": "string", "description": "可选。scope 对应的 ID" },
    "importance": { "type": "number", "description": "可选。1-5，默认 3" },
    "confidence": { "type": "number", "description": "可选。0-1，默认 1" },
    "status": { "type": "string", "enum": ["active", "pending", "archived", "superseded"], "description": "可选。默认 active" },
    "pinned": { "type": "boolean", "description": "可选。是否固定优先召回" },
    "sourceId": { "type": "string", "description": "可选。来源记录 ID" }
  },
  "required": ["bookId", "kind", "content"]
}
```
