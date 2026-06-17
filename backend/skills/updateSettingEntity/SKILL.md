---
name: updateSettingEntity
description: 当需要修改/整理本书已有世界设定条目（地点/势力/物品等）时使用。依赖 getSettingEntities（先拿到 entityId 与现有档案全文）。
---

按 entityId 更新设定条目。name / tags / profileMd 三个字段只覆盖显式传入的项；注意 **profileMd 是整篇覆盖**：必须先用 getSettingEntities 读取现有档案，在其基础上合并整理后传入完整 Markdown，否则会丢失原有设定。删除条目用 `deleteSettingEntity`（需用户明确同意）。

**重要**：本工具采用「提议制」——不会直接写入数据库，而是向用户提交差异预览；用户接受后才会落库。调用成功后返回 `pendingUserApproval: true`，请等待用户确认，**不要**重复调用同一修改，也**不要**自称「已保存/已更新」。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（用于归属校验）" },
    "entityId": { "type": "number", "description": "条目 ID，来自 listSettingEntities / getSettingEntities" },
    "name": { "type": "string", "description": "可选。条目名称" },
    "tags": { "type": "string", "description": "可选。标签，逗号分隔" },
    "profileMd": { "type": "string", "description": "可选。条目档案 Markdown 全文（整篇覆盖，须包含保留的原有内容）" }
  },
  "required": ["bookId", "entityId"]
}
```
