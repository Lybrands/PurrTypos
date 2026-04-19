---
name: deleteSparkIdea
description: 当需要删除一条已有的本书设定时使用（不可恢复）。
---

物理删除本书设定条目。**操作不可撤销**，调用前请务必确认目标 id。

## 何时使用

- 用户**明确要求**删除某条本书设定（"把 XXX 这条设定删掉"/"清掉错的人物年龄设定"等）。
- 用户的修订型请求若可以用 `updateSparkIdea` 改写完成，**优先用 update**，不要走删除。
- 单纯重组/合并多条设定时，倾向 `addSparkIdea` 新增 + `updateSparkIdea` 改写，少用删除。
- 删除伏笔请用对应的伏笔工具，不要用本工具。

## 调用前置条件

1. **必须先用 `searchSparkIdeas` 取到目标条目的 `id`**（结果中以 `[id:N]` 给出），不要凭空猜 id。
2. 在回复用户时，**先复述要删除的内容**，得到用户的明确同意再调用本工具。
3. 一次只删一条；批量删除请逐条调用，便于用户中途叫停。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "number", "description": "要删除的设定条目 id；必须先用 searchSparkIdeas 取到，不能凭空捏造" }
  },
  "required": ["id"]
}
```

## 行为

- 若 `id` 不存在返回 `success=false`。
- 删除成功时返回被删除条目的 `content` 与 `layer`，便于在回复中复述"已删除：XXX"。
- 该条目对应的 FTS 全文索引会自动同步移除。
