---
name: createSettingEntity
description: 当需要为本书新建一个世界设定条目（地点/势力/物品等）时使用。建议先用 listSettingEntities 确认无同名条目，避免重复创建。
---

为本书新增一个设定条目。档案用 Markdown（profileMd）记录，建议按小节组织（如 `## 概述`、`## 关键细节`、`## 与主线的关联`），按需增删小节。人物请用 createCharacter，不要用本工具。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "entityType": { "type": "string", "enum": ["location", "faction", "item", "other"], "description": "条目类型：location 地点 / faction 势力 / item 物品 / other 其他" },
    "name": { "type": "string", "description": "条目名称（必填）" },
    "tags": { "type": "string", "description": "可选。标签，逗号分隔，如 \"主城, 宗门驻地\"" },
    "profileMd": { "type": "string", "description": "可选。条目档案 Markdown 全文" }
  },
  "required": ["bookId", "entityType", "name"]
}
```
