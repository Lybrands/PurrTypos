---
name: getBookCharacters
description: 当需要只读查看本书人物设定详情（可经 id 或角色名筛选）时使用。可选依赖 listBookCharacters（先枚举 id 与姓名）。
---

包含人物的相关信息（姓名、性别、年龄、职业、性格、外貌、背景等）。未传 characterIds 与 names 时返回全部；子集筛选优先 characterIds，否则可用 names。若只要 id 与姓名的 JSON 列表、不要详情，用 listBookCharacters。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "characterIds": {
      "type": "array",
      "items": { "type": "number" },
      "description": "可选。人物 ID 列表，来自返回中的「人物ID」"
    },
    "names": {
      "type": "array",
      "items": { "type": "string" },
      "description": "可选。角色名关键词列表，模糊匹配"
    }
  },
  "required": ["bookId"]
}
```
