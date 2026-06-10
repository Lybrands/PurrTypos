---
name: updateCharacter
description: 当需要修改/整理本书已有人物的设定时使用。依赖 getBookCharacters（先拿到 characterId 与现有档案全文）。
---

按 characterId 更新人物卡。name / tags / profileMd 三个字段只覆盖显式传入的项；注意 **profileMd 是整篇覆盖**：必须先用 getBookCharacters 读取现有档案，在其基础上合并整理后传入完整 Markdown，否则会丢失原有设定。删除人物没有工具，需用户在「人物」面板手动操作。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（用于归属校验）" },
    "characterId": { "type": "number", "description": "人物 ID，来自 listBookCharacters / getBookCharacters" },
    "name": { "type": "string", "description": "可选。人物姓名" },
    "tags": { "type": "string", "description": "可选。标签，逗号分隔" },
    "profileMd": { "type": "string", "description": "可选。人物档案 Markdown 全文（整篇覆盖，须包含保留的原有内容）" }
  },
  "required": ["bookId", "characterId"]
}
```
