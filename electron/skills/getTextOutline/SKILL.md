---
description: 只读获取本书「文本大纲」Markdown（左侧大纲各条目的文本标签页），与章节树 chaptersText 互补。
---

当用户讨论**提纲、剧情线、在文本大纲里写的内容**时调用；若只要 XMind/目录树结构，用 `batchGetOutlineDetails` 或 `getAllOutlines`。

参数：`bookId` 必填；`outlineIds` 可选以缩小范围；`maxTextLength` 可选（默认 32000）。
