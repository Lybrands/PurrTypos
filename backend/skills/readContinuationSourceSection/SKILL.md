---
name: readContinuationSourceSection
description: 续写作品需要核对冻结来源在分叉点以前的某一完整章节时使用。来源只读，宿主会校验准确版本和分叉边界。
---

省略 sectionId 时返回可分页的历史目录（合法 sectionId、标题、顺序与卷定位）；传入 sectionId 时读取一章。不能读取分叉点之后的来源内容，不能修改来源、正史快照或来源分析。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "string", "description": "当前续写作品 ID，由宿主绑定" },
    "offset": { "type": "integer", "minimum": 0 },
    "limit": { "type": "integer", "minimum": 1, "maximum": 200 },
    "sectionId": { "type": "string", "description": "来源章节 ID，必须属于冻结版本且不晚于分叉点" }
  },
  "required": ["bookId"]
}
```
