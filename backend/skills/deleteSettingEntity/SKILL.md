---
name: deleteSettingEntity
description: 当用户明确要求删除本书某个世界设定条目（地点/势力/物品等）时使用（不可恢复，连同其修订历史一起删除）。
---

物理删除本书的一个世界设定条目及其全部修订历史。**操作不可撤销**，调用前请务必确认目标条目。

## 何时使用

- 用户**明确要求**删除某个设定条目（"把 XXX 这个地点删掉"/"这个门派不要了"等）。
- 修订型请求（改名、合并、整理内容）请用 `updateSettingEntity` 完成，**不要走删除重建**。
- 删除人物请用 `deleteCharacter`，不要用本工具。

## 调用前置条件

1. **必须先用 `listSettingEntities` / `getSettingEntities` 取到目标条目的 `entityId`**，不要凭空猜 id。
2. 在回复用户时，**先复述要删除的条目名称与类型**，得到用户的明确同意再调用本工具。
3. 一次只删一条；批量删除请逐条调用，便于用户中途叫停。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（用于归属校验）" },
    "entityId": { "type": "number", "description": "要删除的条目 ID；必须来自 listSettingEntities / getSettingEntities，不能凭空捏造" }
  },
  "required": ["bookId", "entityId"]
}
```

## 行为

- 若 `entityId` 不属于当前书籍或不存在，返回 `success=false`。
- 删除成功时返回被删除条目的 `name`，便于在回复中复述"已删除：XXX"。
- 该条目的修订历史会一并删除。
