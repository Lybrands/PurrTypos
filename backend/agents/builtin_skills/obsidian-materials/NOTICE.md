# 修改说明

本包的 `references/ofm-syntax.md` 基于 [kepano/obsidian-skills](https://github.com/kepano/obsidian-skills) 仓库中 `obsidian-markdown` 技能（skills/obsidian-markdown/SKILL.md 与 references/CALLOUTS.md）修剪而来，上游以 MIT 许可证发布（见同目录 [LICENSE.txt](LICENSE.txt)，Copyright (c) 2026 Steph Ango (@kepano)）。

相对上游的修改：

- 删除 Internal Links (Wikilinks)、Embeds、Properties (Frontmatter) 章节，以及 Complete Example 中的 frontmatter 与 wikilink 示例——这些在 PurrTypos 中由宿主契约管理（链接用 materialLink，frontmatter 宿主专属），不适用于模型写作。
- Callout 参考并入正文语法参考；标签一节改写为「仅理解既有笔记用」并指向本包契约。
- 包入口 `SKILL.md` 为 PurrTypos 自有内容，与上游无关。

同步上游：上游更新时重新下载对应技能文件、重放上述删节即可；本包 versionId 由文件树内容决定，内容变化即产生新版本。
