---
name: deleteCharacter
description: 当用户明确要求删除本书某个人物卡时使用（不可恢复，含其修订历史归档）。
---

物理删除本书的一个人物卡。**操作不可撤销**，调用前请务必确认目标人物。

## 何时使用

- 用户**明确要求**删除某个人物（"把 XXX 这个角色删掉"/"龙套甲不要了"等）。
- 修订型请求（改名、改设定、合并人设）请用 `updateCharacter` 完成，**不要走删除重建**。
- 用户只是说"这个角色写得不好"之类的评价，**不构成删除指令**，应先确认意图。

## 调用前置条件

1. **必须先用 `listBookCharacters` / `getBookCharacters` 取到目标人物的 `characterId`**，不要凭空猜 id。
2. 在回复用户时，**先复述要删除的人物名字与关键设定**，得到用户的明确同意再调用本工具。
3. 一次只删一个人物；批量删除请逐个调用，便于用户中途叫停。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID（用于归属校验）" },
    "characterId": { "type": "number", "description": "要删除的人物 ID；必须来自 listBookCharacters / getBookCharacters，不能凭空捏造" }
  },
  "required": ["bookId", "characterId"]
}
```

## 行为

- 若 `characterId` 不属于当前书籍或不存在，返回 `success=false`。
- 删除成功时返回被删除人物的 `name`，便于在回复中复述"已删除：XXX"。
- 正文中已写到该人物的内容**不会**被改动；只删除设定面板里的人物卡。
