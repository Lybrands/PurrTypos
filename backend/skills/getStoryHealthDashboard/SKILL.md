---
name: getStoryHealthDashboard
description: 当需要查看本书仪表盘故事健康度、伏笔逾期/临近、人物久未出场、整体章节/字数概况时使用。只读聚合工具。
---

返回压缩 JSON，包含：
- 总章节数、已写章节数、总字数、最新已写章节序号
- 未回收伏笔数量、逾期数量、临近回收数量、重点未回收伏笔
- 人物出场统计与久未出场排行

适用场景：
- 判断某个角色是否长时间未出场
- 检查伏笔是否逾期或临近回收
- 评估当前故事健康度、节奏风险、人物出场空档

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
