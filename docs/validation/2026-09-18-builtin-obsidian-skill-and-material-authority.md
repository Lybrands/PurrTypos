# 内置 Obsidian 技能与写作 Agent 资料文件权威

日期：2026-09-18。

## 范围

三件相互依赖的事：

1. **写作 Agent 资料读写接入共享文件权威**。此前 PurrA 原生读写路径（`agents/writing/read_model.py`、`material_write_model.py`）直接读写 SQLite 投影，绕过了 `@material_crud` 权威层：共享资料作品上不返回 `materialLink`、不做链接规范化与同名拒绝、不更新 Markdown 文件、无基线保护，baseRevision 也与文件版本脱钩。
2. **「技能」成为独立模块**（与写作技法分开）：内置技能（随应用发布）与已安装技能（用户导入）同库不同来源；技能不参与按书授权、手动/自动模式与检索选择。**是否允许 Agent 自动使用由技能包 SKILL.md 的 `metadata.autoUse` 声明**（默认否）——声明的技能进入运行冻结快照，Agent 用 `listWritingSkills`（元数据供相关性自动匹配）与 `readWritingSkillFile`（SKILL.md 先读门控）读取；未声明的技能仅在技能库页面供用户查看。
3. **内置技能 `builtin-obsidian-materials`**：以 kepano/obsidian-skills（MIT）的 obsidian-markdown 技能修剪版为语法参考，入口 SKILL.md 为 PurrTypos 资料契约（链接只用 materialLink、不写 frontmatter、正文不加 #标签、只读继承基线、允许语法及 App 预览局限、禁用 `![[…]]` 嵌入），`metadata.autoUse: true`。

## 实现边界

- **读**（`read_model.py`）：详情读（include_profile / 故事背景）先 `synchronize` 再 `annotate`——baseRevision 换为文件版本，附带 `materialId` / `materialLink` / `inheritedBaseline` / `inheritedEvidence`；目录读轻量附 `materialLink`。未绑定作品行为不变。
- **写**（`material_write_model.py`）：绑定作品在提交事务内 `synchronize → normalize_links → svc.update`（文件版本 CAS + 两阶段文件提交），创建走 `svc.add`，删除走权威删除（基线保护 + 回收站）；回执 `committedRevision` 为文件版本。冲突/同名/基线/权威不可用分别映射独立错误码。未绑定作品保留原 record_revision CAS。
- **技能存储**：`TechniqueFileStore` 参数化 `collection`/`record_kind` 后由 `SkillFileStore` 子类获得第三集合 `skills/`（同一 writing-library 根与锁）；catalog 复用 `writing_technique_catalog` 的 `kind='skill'` 行；备份校验增加第三 store。service 层 `origin: "builtin"` 守卫：内置技能禁删/禁归档/禁用户起草发布（种子通道 `internal=True` 旁路）。
- **种子与安装**：`application/builtin_skills.py` 启动幂等发布（operation id 绑定包内容 versionId 前缀，中断重放、升级出新版本、内容不变跳过），挂接 `main.py` lifespan。安装走 `/skills/import-preview`（流式 zip/md，复用技法包守卫）→ `/skills/import`（导入→封存→发布一次完成，返回 autoUse 状态）。
- **Agent 面**：`context_snapshot.py` 冻结 `skills` 数组（仅 autoUse=true 且 active 已发布，含 ref/metadata/entryBytes，随 run 绑定属性冻结，版本内容寻址自校验）；`context_tools.py` 新增 `listWritingSkills` / `readWritingSkillFile`（SKILL.md 先读门控、maxTextLength 上限、冻结成员校验、`writingSkillEntryRefs` 粘性记录）。不触碰授权/模式/选择机制。
- **页面**：`/skills` 路由的技能库页面（AppHeader + 安装技能 + 来源筛选「全部/内置技能/已安装技能」+ 卡片网格带来源与自动使用徽标 + 只读详情弹窗含文件树/Markdown/导出；已安装技能可归档/永久删除，内置显示「随应用更新」）；书架导航新增「技能库」入口。
- 工具描述补充 materialLink 契约提示（getBookCharacters / getSettingEntities / getStoryBackground / 各写入工具）。

## 证据

- `tests/test_writing_replacement_material_authority.py`（6 例）：绑定读携带文件版本与 materialLink；更新提交文件并拒绝过期版本；模型手写链接规范化与同名拒绝；创建登记材料文件且回执版本即后续 baseRevision；删除尊重基线并进回收站；背景编辑保留继承基线。
- `tests/test_builtin_skills.py`（6 例）：技能种子发布与幂等；升级发布新版本且旧版本保留；内置防用户改动；安装→列表→读取→归档→删除；快照只收录 autoUse=true；工具门控（先读 SKILL.md、未声明 autoUse 拒绝、冻结外版本拒绝、maxTextLength 提示实际大小）。
- 全量后端套件：除 3 个既有 screenplay 失败（与本次改动无关，见 2026-09-17 会话记录）外全部通过；lifespan 路由计数更新为 25。
- 前端 `tsc --noEmit` 与 `vite build` 通过。
- 上游来源：kepano/obsidian-skills（MIT，Copyright 2026 Steph Ango），修剪说明见技能包内 NOTICE.md。

## 已知边界

- App 内 Markdown 预览不渲染 callout/高亮/脚注/Mermaid（技能中以表格标注）；渲染器增强为长期项。
- 绑定作品上升级前会话读取的 record_revision 令牌首次提交会 CAS 失败，模型按冲突提示重读即可。
- 技能无应用内编辑（更新靠重装或应用升级）；技能读取不计入技法 usage 面板；技能仅对写作 Agent 开放。
- autoUse 以包内声明为唯一事实源，无用户级覆盖开关（避免双源）。
