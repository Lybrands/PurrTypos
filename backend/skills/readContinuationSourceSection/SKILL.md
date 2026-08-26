---
name: readContinuationSourceSection
description: 续写作品需要核对冻结来源在分叉点以前的某一完整章节时使用。来源只读，宿主会校验准确版本和分叉边界。
---

只读取当前续写作品已冻结来源版本中的一章。不能读取分叉点之后的来源内容，不能修改来源、正史快照或来源分析。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前续写作品 ID，由宿主绑定" },
    "sectionId": { "type": "string", "description": "来源章节 ID，必须属于冻结版本且不晚于分叉点" }
  },
  "required": ["bookId", "sectionId"]
}
```
