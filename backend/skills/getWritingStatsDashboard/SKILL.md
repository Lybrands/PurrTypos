---
name: getWritingStatsDashboard
description: 当需要查看本书仪表盘写作统计、今日新增字数、日更目标、近日日更、章节字数分布、空章节时使用。只读聚合工具。
---

返回压缩 JSON，包含：
- 总字数、今日新增、日更目标、平均章节字数
- 最近 14 天日更数据
- 字数最多章节与空章节列表

适用场景：
- 评估写作进度、日更完成情况、章节长度分布
- 判断哪些章节过长、过短或尚未开始
- 结合章节内容分析节奏和产出状态

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" }
  },
  "required": ["bookId"]
}
```
