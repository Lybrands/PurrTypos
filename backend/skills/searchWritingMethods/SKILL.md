---
name: searchWritingMethods
description: 仅当用户明确请求推荐写作方法时，检索只读写作方法目录并返回建议候选。
---

检索已发布的写作方法目录。只返回建议和理由，不得绑定、升级、解绑或调整作品写作方法。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string", "description": "用户当前写作目标或检索关键词" },
    "limit": { "type": "integer", "minimum": 1, "maximum": 20, "description": "最多返回条数，默认 8" }
  },
  "required": ["query"]
}
```
