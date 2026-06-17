---
name: listSettingEntities
description: 当只需要本书世界设定条目（地点/势力/物品等）的 id、类型与名称列表、不要详情时使用。
---

示例：`[{"id":2,"type":"location","typeLabel":"地点","name":"青云山"}]`。与 getSettingEntities 互补。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" }
  },
  "required": ["bookId"]
}
```
