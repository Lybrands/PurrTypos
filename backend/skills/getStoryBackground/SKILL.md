---
name: getStoryBackground
description: 当需要只读查看本书「小说背景」整块文档（世界观、基调、总设定等）时使用。
---

小说背景文档正文；与人物工具组合可回答「背景+角色」类问题。

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
