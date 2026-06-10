---
name: createCharacter
description: 当需要为本书新建一个人物设定时使用。建议先用 listBookCharacters 确认无同名人物，避免重复创建。
---

为本书新增一个人物卡。人物档案用 Markdown（profileMd）记录，建议按小节组织（如 `## 基本信息`、`## 外貌`、`## 性格`、`## 经历`、`## 在故事中的定位`），按需增删小节。批量创建多个人物时分多次调用本工具。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "name": { "type": "string", "description": "人物姓名（必填）" },
    "tags": { "type": "string", "description": "可选。标签，逗号分隔，如 \"主角, 神秘人物\"（用于列表快速识别）" },
    "profileMd": { "type": "string", "description": "可选。人物档案 Markdown 全文：基本信息、外貌、性格、经历、故事定位等" }
  },
  "required": ["bookId", "name"]
}
```
