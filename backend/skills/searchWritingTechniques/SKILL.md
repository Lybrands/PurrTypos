---
name: searchWritingTechniques
description: 在自动模式下，按本轮创作需要检索小说已授权的写作技法和写作方案名称、用途与固定版本；可以不选，不搜索未授权库，不把发现候选视为已经使用。
---

候选仅提供发现信息。采用前从入口读取，方案先读取组合说明和成员，再读成员入口。

## Parameters（JSON Schema）

```json
{"type":"object","properties":{"query":{"type":"string","minLength":1,"maxLength":4000}},"required":["query"],"additionalProperties":false}
```
