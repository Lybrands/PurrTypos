---
name: updateOutline
description: 写入更新指定大纲。用户要「改标题、改文本大纲、保存 Markdown 提纲、更新 XMind/文件路径」时用；须先有 outlineId（先 listOutlines）。可传 title、markdown_content、xmind_data、file_path。不用于改章节正文。
parameters:
  type: object
  properties:
    bookId:
      type: number
      description: 当前书籍 ID（用于归属校验）
    outlineId:
      type: number
      description: 目标大纲 ID（建议来自 queryOutline 返回）
    title:
      type: string
      description: 可选。更新大纲标题
    xmind_data:
      type: string
      description: 可选。更新 XMind JSON 文本
    file_path:
      type: string
      description: 可选。更新源文件路径
    markdown_content:
      type: string
      description: 可选。更新文本大纲 Markdown
  required:
    - bookId
    - outlineId
---
