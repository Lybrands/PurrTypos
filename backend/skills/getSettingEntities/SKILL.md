---
name: getSettingEntities
description: 当需要查看本书世界设定条目（地点/势力/物品等）的完整档案时使用。可按 entityIds / names / entityType 过滤。
---

返回各条目的类型、名称、标签与 Markdown 档案全文。修改条目前必须先用本工具读取现有档案全文。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "entityIds": { "type": "array", "items": { "type": "number" }, "description": "可选。按条目 ID 过滤（来自 listSettingEntities）" },
    "names": { "type": "array", "items": { "type": "string" }, "description": "可选。按名称模糊过滤" },
    "entityType": { "type": "string", "enum": ["location", "faction", "item", "other"], "description": "可选。按类型过滤：location 地点 / faction 势力 / item 物品 / other 其他" }
  },
  "required": ["bookId"]
}
```
