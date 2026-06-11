---
name: editStoryBackground
description: 当需要修改或整理本书的故事背景/世界观设定（Markdown）时使用。依赖 getStoryBackground（先读现状再整体改写）。
---

覆盖写入小说背景全文。这是**整篇覆盖**而非追加：必须先用 getStoryBackground 读取现有内容，在其基础上合并、整理后传入完整 Markdown，否则会丢失原有设定。

**重要**：本工具采用「提议制」——不会直接写入数据库，而是向用户提交差异预览；用户接受后才会落库。调用成功后返回 `pendingUserApproval: true`，请等待用户确认，**不要**重复调用同一修改，也**不要**自称「已保存/已更新」。

## Parameters（JSON Schema）

```json
{
  "type": "object",
  "properties": {
    "bookId": { "type": "number", "description": "当前书籍 ID" },
    "content": { "type": "string", "description": "小说背景 Markdown 全文（覆盖写入，须包含保留的原有内容）" }
  },
  "required": ["bookId", "content"]
}
```
