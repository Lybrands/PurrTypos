---
name: obsidian-materials
description: 共享资料（Obsidian vault）写作契约：资料链接一律使用读取结果返回的 materialLink，绝不手写 frontmatter，正文可用哪些 Obsidian 语法，以及只读继承基线规则。修改人物、世界设定或故事背景资料前必读。
tags:
  - 内置
  - 资料契约
  - Obsidian
metadata:
  autoUse: true
  retrieval:
    intents:
      - 修改人物设定或世界设定资料
      - 编辑故事背景
      - 在资料正文中引用另一条资料
      - 新增或删除设定实体
      - 处理资料版本冲突或同名资料
    contexts:
      - 共享资料（Obsidian）模式的作品
      - 使用 updateCharacter、updateSettingEntity、editStoryBackground、createSettingEntity 时
      - 读取结果附带 materialLink 或 inheritedBaseline 时
    keywords:
      - materialLink
      - 双链
      - wikilink
      - frontmatter
      - 继承基线
      - 原作继承
      - 资料
      - 设定
      - 人物卡
      - 故事背景
      - baseRevision
    exclusions:
      - 小说正文写作
      - 剧本写作
      - 写作技法内容本身
---

# 共享资料写作契约

作品启用共享资料后，人物、世界设定、故事背景以 Markdown 文件为唯一事实源，PurrTypos 与 Obsidian 指向同一份文件。写作 Agent 通过资料工具读写这些文件。本文件是修改资料正文前必须遵守的契约；通用 Obsidian 语法细节见 [ofm-syntax.md](references/ofm-syntax.md)。

## 资料链接：只用 materialLink，不手写 `[[…]]`

- 引用另一条资料（人物关系、设定关联）时，**原样粘贴读取结果返回的 `materialLink`**，例如 `[[资料/人物/沈青--a1b2c3d4e5f6|沈青]]`。
- **不要自己拼 `[[名字]]` 这类裸链接**：同名资料会被直接拒绝，唯一的模糊引用会被系统改写。改写结果可能与你想指的目标不一致。
- 关系的含义写在正文里（一句话说明两人是什么关系、设定之间如何关联），链接只负责指向。
- 修改关系前先读取目标资料；删除关系时移除对应引用。
- 小说正文中不加任何资料链接。

## frontmatter：绝不自己写

- 每条资料文件开头的 YAML frontmatter（`purr_schema`、`purr_id` 等）由宿主管理，**任何情况下不要产出 YAML frontmatter**。
- 提交给资料工具的正文（profileMd / content）**不得以 `---` 块开头**——宿主会在文件头拼接正式 frontmatter，正文再以 `---` 开头会产生双头 YAML，破坏文件解析。

## 标签：由宿主管理

- 资料标签存放在 frontmatter 的 `purr_tags`，通过资料工具的 tags 字段更新。
- **不要在正文中随手加 `#标签`**：Obsidian 会把它们索引成正式标签，与宿主管理的标签体系冲突。

## 只读继承基线

- 续写作品的资料可能带有 `inheritedBaseline`（原作继承基线）。它只读，**不可改写、不可删除**，续写内容写在正文里。
- 资料编辑工具提交的正文只包含可编辑部分；基线由宿主在文件中自动保留，你不需要、也不应该在正文中重现基线内容。
- 带基线的设定实体不能删除，只能修改本书后续发展。

## 正文允许的 Obsidian 语法

以下语法可出现在资料正文（profileMd / content）中，用于组织设定信息：

| 语法 | 写法 | App 内预览 | 仅 Obsidian 渲染 |
| --- | --- | --- | --- |
| 标题 / 列表 / 表格 / 加粗 | 标准 Markdown | ✅ | ✅ |
| Callout 提示块 | `> [!note] 标题` | ❌ 显示原文 | ✅ |
| 行内代码与代码块 | `` `代码` `` | ✅ | ✅ |
| 注释（阅读时隐藏） | `%%注释%%` | ❌ | ✅ |
| 高亮 | `==重点==` | ❌ 显示原文 | ✅ |
| 脚注 | `正文[^1]` | ❌ | ✅ |
| 数学公式 | `$e^{i\pi}$` | ❌ | ✅ |
| Mermaid 图 | ` ```mermaid ` | ❌ | ✅ |

- 「App 内预览」列指 PurrTypos 编辑器内是否渲染；即使 App 内显示原文，Obsidian 中仍正常渲染，可以放心使用。
- **不要使用 `![[…]]` 嵌入**把整条资料内联进另一条资料：内容会重复，检索与差异审阅都会变大变慢。引用一律用 materialLink 链接。

各语法的完整写法与 Callout 类型清单见 [ofm-syntax.md](references/ofm-syntax.md)。

## 工作流

1. **先读后写**：`getBookCharacters` / `getSettingEntities` / `getStoryBackground` 读取目标资料，拿到 `baseRevision`（版本凭证）与 `materialLink`。
2. **改正文**：基于读到的内容修改，遵守上面的链接、frontmatter、标签、基线规则。
3. **提交**：更新工具携带 `baseRevision` 提交；回执中的 `committedRevision` 可直接作为下一次提交的 `baseRevision`。
4. **冲突重读**：提交被版本冲突拒绝时，说明资料在 Obsidian 或其他入口被改过——重新读取再改，不要盲目重试。
