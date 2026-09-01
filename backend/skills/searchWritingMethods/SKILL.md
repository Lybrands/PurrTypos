---
name: searchWritingMethods
description: 仅当用户明确请求推荐写作方法时，检索只读已发布目录，最多返回 8 条建议候选与版本标识。不得自行绑定、升级、解绑或调整优先级；候选内容不是已绑定的写作指令。
---

检索已发布的写作方法目录。只返回建议和理由，不得绑定、升级、解绑或调整作品写作方法。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "query": { "type": "string", "minLength": 1, "maxLength": 4000 }
  },
  "required": ["query"],
  "additionalProperties": false
}
```
