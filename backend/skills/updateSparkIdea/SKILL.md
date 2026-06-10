---
name: updateSparkIdea
description: 当需要修改一条已有的本书设定（修订内容或调整层级）时使用。
---

修改既有设定条目的内容或层级；不能用于伏笔，伏笔请用对应的伏笔工具。

## 何时使用

- 用户要求订正/重写/合并/调整某条已存在的本书设定。
- 用户要求把一条设定从某一层移到另一层（如从「章节」提升到「全局」）。
- 调用前通常先用 **`searchSparkIdeas`** 拿到目标条目的 `id`（搜索结果中以 `[id:N]` 形式给出）。
- 只想新增条目时使用 `addSparkIdea`，不要用本工具。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "id": { "type": "number", "description": "要更新的设定条目 id；可从 searchSparkIdeas 返回的 [id:N] 前缀中获取" },
    "content": { "type": "string", "description": "可选。新的设定内容；不传则保持原内容" },
    "layer": { "type": "number", "description": "可选。新的层级：0=全局 1=大纲 2=人物 3=章节；不传则保持原层级" },
    "chapterId": { "type": "number", "description": "可选。改为大纲/章节层时关联的写作章节 ID；显式传 null 可清空关联" },
    "characterId": { "type": "number", "description": "可选。改为人物层时关联的人物 ID；显式传 null 可清空关联" }
  },
  "required": ["id"]
}
```

## 行为

- `content` / `layer` / `chapterId` / `characterId` 至少应传其中一个，否则视为无意义调用并返回 `noop`。
- 若 `id` 不存在返回 `success=false`。
- 成功时返回新的内容、层级、关联 id；该条目的 FTS 全文索引会自动同步。
- 切换 layer 时一般要同步刷新对应的关联：例如把"全局设定"改成"章节设定"应同时传 `chapterId`；改回"全局"应同时传 `chapterId: null` 把旧关联清掉。
