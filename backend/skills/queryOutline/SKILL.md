---
name: queryOutline
description: 当需要只读查看本书某条大纲内容时使用（同时返回思维导图与文本大纲）。依赖 listOutlines（先获取真实 outlineId）。
---

仅支持通过 **outlineId / outlineIds** 查询；禁止 outlineTitle / outlineIndex。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（可由宿主注入）" },
    "outlineIds": {
      "type": "array",
      "items": { "type": "string" },
      "description": "仅查询这些大纲 id（与 outlineId 二选一，必须提供其一）"
    },
    "outlineId": { "type": "string", "description": "单条大纲 id（与 outlineIds 二选一，必须提供其一）" },
    "maxTextLength": { "type": "number", "description": "可选。文本大纲最大长度，默认 32000" }
  },
  "required": ["bookId"]
}
```

> 调用约束：`outlineId` 与 `outlineIds` 二选一必须传；仅传 `bookId` 将被后端拒绝（服务端会在执行阶段强制校验）。
