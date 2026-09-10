# 导演工作台（Director Workspace）设计共识

> **状态**：v1 共识，待 plan & 实施
> **范围**：`src/Workspace` 整体重设计，更侧重 AI 体验
> **下次接续**：从「待议清单」或「分阶段实施建议」任意一条直接展开

---

## 一句话定位

> **作者确定创作意图，AI 提供正文提案。** 工作台默认以 AI 为中心，但任意一栏（大纲 / AI / 写作）都能一键提升为主区域。

---

## 整体架构：三栏可换主

物理位置不变，宽度按"谁是主"重新分配。

### 默认 · AI 为主（导演模式开机态）

```
┌──────────────┬───────────────────────────┬──────────────┐
│ 📓 导演笔记本 │     🤖 AI 主区域          │ ✍️ 写作      │
│ (侧 18%)     │     (主 56%)              │ (侧 26%)     │
│              │                           │              │
│ 设定/章节/   │  对话流 · 流式生成        │ [正文][Canvas]│
│ 风格 ...     │  ↳[改正文][插光标][留chat]│              │
│              │                           │ Lexical 编辑 │
│              │  输入框 + @胶囊 + 控制    │              │
└──────────────┴───────────────────────────┴──────────────┘
```

### 主区域切换

每栏顶部加 **「⤢ 扩大此区域」** 显式按钮（可发现性优先于隐性）。

- 点击该按钮 → 该栏变为 56%，其他两栏缩成 22% 摘要视图
- 再次点击或回到 AI 主：恢复默认
- **侧栏不是折叠**，仍可见可操作，只是变成"摘要视图"

### 两种使用模式（顶栏开关）

| 模式 | 默认主区域 | AI 触达方式 | 适合场景 |
|---|---|---|---|
| 🎬 **导演模式** | AI 主 | 大对话流为主，diff 落地 | 给意图 / 审稿 / 重写 |
| 📝 **写作模式** | 写作主 | AI 缩成右侧浮岛 + 内联 ghost text + Inline Edit | 自己打字落笔为主 |

模式切换是**同一组件的不同布局比例 + AI 行为风格**，不是两套 UI。

---

## 左栏：📓 导演笔记本

### 设计原则

**完全废弃现有 `OutlinePanel`**，所有内容内嵌到导演笔记本（**B_inline + delete OutlinePanel**）。

`CharacterTab` / `StoryBackgroundTab` / `OutlineMarkdownPane` / `MindMapView` 改造为**紧凑内嵌组件**（左栏内联编辑，无弹窗）。

### 笔记本分组

```
▼ 🎭 人物         [+]
  ☑ 李雷 ▾   ← 展开内联表单（年龄/性格/外貌/...）
  ☑ 韩梅梅
▼ 🌍 故事背景     [+]
  ☑ 主线 ▾   ← 展开内联 tiptap
▼ 📕 章节
  ▸ 第1章   ☑ 作为参考
  ● 第3章 (当前)
▼ 🌳 大纲
  ☑ 全书总纲
▼ 🧠 记忆 / 伏笔  [+]
▼ 🎨 风格基调     [编辑]
─────────────
N 项已勾选 → 注入 AI 上下文
```

### 复选框 = AI 上下文勾选

- 勾选项 → 强制注入 AI 的 system prompt
- 未勾选项 → AI 可通过 skill 按需自取（见"AI 上下文策略"）

---

## 中栏：🤖 AI 主区域

复用现有 `AiPanel` 的全部能力（多会话、记忆、prompt 模板、Agent 工具与人工审批），作为主舞台。

### AI 输出三种落点

每段 AI 输出右上角挂三个按钮：

| 按钮 | 行为 | 适用场景 |
|---|---|---|
| **改正文 (diff)** | 在 Canvas/正文里以 diff 形式插入，逐段接受 | 改写、润色 |
| **插入光标** | 直接插到光标位置 | 续写、补段 |
| **留 chat** | 不动正文 | 灵感、咨询 |

### 模式（chatAgentMode）

| 模式 | 现职 | 新 UI 下的角色 |
|---|---|---|
| `ask` | 纯问答 | 不出现 diff 按钮（不改正文）|
| `agent` | 三层 Writing Agent + 工具 | 导演模式默认 |

模式选择器位置：AI 输入框上方的下拉（同现状），不退化到"高级设置"。

---

## 右栏：✍️ 写作 + Canvas

### 两个 Tab

| Tab | 内容 | 持久化 | AI 落地 |
|---|---|---|---|
| **正文** | 当前章节实际内容 | `articles` 表 | diff 接受 / 直接插入 |
| **Canvas 草稿** | AI 实验性产出的草稿 | 新表 `chapter_canvas`（一对一）| AI 写到这里 → 用户点 [合并到正文] → 走 diff |

### Canvas 设计要点

- **单 Canvas**：每章一个 Canvas（`chapter_canvas` 表，与 `articles` 一对一）
- **持久化**：关闭重开仍在
- **不并排对比**（避免三栏挤爆，靠主区域切换实现"放大"）
- **未来扩展**：多版本 fragments 暂不做，等用户需要"AI 给我多个候选挑一个"再加

---

## diff 接受/拒绝体验

### 颗粒度

- **段落级为主**（中文写作的天然单位）
- **整章重写时升级为「全章一次接受 / 全章一次拒绝」**（避免几十个 diff 块过碎）

### 拒绝默认行为

- 默认：**直接原位再生成**（最快迭代）
- 下拉选项：「说明理由」「放弃」（精准反馈或彻底丢弃）

### 其他约定

- **批量按钮**：浮在编辑器顶部，「全部接受 / 全部拒绝」
- **并发锁**：AI 改某段时，**其他段进入只读**，防止冲突写入
- **diff 历史**：接受后仍能在「diff 历史」面板查看原文 / 回滚

---

## 风格基调（每本书一份）

### 字段（minimal 集）

| 字段 | 类型 | 用途 |
|---|---|---|
| 视角 | radio (第一人称 / 第三人称 / 全知) | 注入 system prompt |
| 语气基调 | radio + 自定义 (悬疑 / 冷峻 / 幽默 / 热血 / ...) | 注入 system prompt |
| 节奏 | slider (慢热 ↔ 快节奏) | 注入 system prompt |
| 禁用规则 | text[] (一行一条) | 强约束注入 |
| 风格参考章节 | EntityId[] | few-shot，AI 模仿其文笔 |
| 自由补充 | text | 兜底自然语言 |

### 数据存储

新表：

```sql
book_style (
  book_id PK,
  narrative_pov,
  tone,
  pace,
  banned_rules JSON,
  reference_chapter_ids JSON,
  free_notes TEXT
)
```

### 参考章节选取

- **手动勾选**为主（用户控制权）
- **AI 也可以通过 skill 主动获取**（`getChapterContent` / `batchGetChapterContents`）按需取章节
- 不做"自动选最近 N 章"（避免 AI 失控）

---

## AI 上下文注入策略（两层模型）

| 层 | 内容 | 时机 |
|---|---|---|
| **强制注入** | 风格基调全字段、参考章节正文、当前章节标题、笔记本里勾选的人物/设定/大纲 | 每次对话自动 |
| **按需获取** | 未勾选的章节正文、人物详情、记忆/伏笔搜索、大纲细节 | AI 调 skill 自取 |

**Token 预算超限时的优先级**（从高到低保留）：

1. 基础角色 prompt
2. 用户当前指令
3. 风格规范字段
4. 当前光标段落
5. 关联人物 / 设定（笔记本勾选的）
6. 风格参考章节
7. 历史对话

---

## 数据模型新增

| 表 | 用途 | 关键字段 |
|---|---|---|
| `book_style` | 风格基调 | book_id, narrative_pov, tone, pace, banned_rules, reference_chapter_ids, free_notes |
| `chapter_canvas` | 章节草稿区 | chapter_id (PK), content, updated_at |
| `chapter_diff_history` | diff 历史 | id, chapter_id, before_text, after_text, accepted_at, rejected_at |

---

## 现有代码归宿

| 现有 | 在新设计中 |
|---|---|
| `OutlinePanel/index.tsx`（顶层 Tabs） | **整体删除** |
| `OutlinePanel/CharacterTab.tsx` | 改造为左栏内嵌组件（人物分组的展开内容） |
| `OutlinePanel/StoryBackgroundTab.tsx` | 改造为左栏内嵌组件 |
| `OutlinePanel/OutlineMarkdownPane.tsx` | 改造为左栏内嵌组件（大纲分组） |
| `OutlinePanel/MindMapView.tsx` | 大纲项展开为思维导图时复用，可能进 modal |
| `EditorPanel/index.tsx`（章节列表 + 编辑器） | **章节列表挪到笔记本**，只保留编辑器 + 工具栏 |
| `EditorPanel/LexicalEditor.tsx` | 复用，加 diff 渲染层 + ghost text 扩展 |
| `AiPanel/*` | 主体复用，作为中栏主舞台；输出消息加"三按钮" |
| `AiContextBar` | 简化，与笔记本勾选打通 |

---

## 待议清单（下次接着聊）

1. ~~第 6 项 · 导演笔记本整合策略~~ ✅ B_inline + delete OutlinePanel
2. ~~第 1 项 · 风格基调字段形态~~ ✅ minimal 字段集
3. ~~第 4 项 · 自动注入 system prompt~~ ✅ 两层模型
4. ~~第 2+3 项 · diff 颗粒度 + 被拒回路~~ ✅ 段落级 + 直接再生成
5. ~~第 7 项 · chatAgentMode 去留~~ ✅ 收口为 `ask` / `agent` 两种模式
6. ~~第 5 项 · 写作模式里的内联 AI~~ ✅ ghost text / Inline Edit / ⌘K 全部交付
7. **左栏在主区域切换时的「摘要视图」具体长什么样**（当前简单 width 压缩，未做真·摘要化，等使用反馈）
8. **「AI 改正文时其他段只读」的视觉表现**（灰化？锁图标？）— 当前 MVP 用全章覆盖层代替段级锁
9. ~~第 9 项 · diff 历史面板的入口~~ ✅ 章节标题旁 ⏱ 图标 → Drawer
10. **新版上下文勾选的"全选"和"清空"快捷**（人物全选 / 当前章节相关全选）
11. ~~多书切换时风格基调和勾选状态的隔离~~ ✅ 阶段 7 完成（associated_context / active_chapter 均按 bookId 隔离）
12. **Canvas 的合并冲突**（用户在 Canvas 工作期间，AI 也改了正文怎么办）

---

## 分阶段实施建议

### 阶段 1 · 基础重构（不改 AI 体验，先搬家）
1. 创建 `DirectorNotebook` 组件占位（左栏新组件）
2. 把 `EditorPanel` 里的章节列表抽出到导演笔记本
3. 把 `OutlinePanel` 三个 Tab 内容内嵌到导演笔记本对应分组
4. 删除 `OutlinePanel` 顶层组件，工作区切换为「左 笔记本 / 中 编辑器 / 右 AI」
5. **此阶段完成后**：用户可在新 UI 下使用当前 Writing Agent

### 阶段 2 · 主区域切换
1. 三栏顶部加 ⤢ 按钮 + 状态机
2. 各栏的「摘要视图」（侧 22% 时显示什么）
3. 顶栏「导演 / 写作」模式开关 + 比例预设

### 阶段 3 · 风格基调
1. `book_style` 表 + 后端 CRUD
2. 笔记本「风格基调」分组的内联编辑表单
3. system prompt 拼装器（两层注入策略）
4. 风格参考章节选择器（复用 `AssociatedChapterSelect`）

### 阶段 4 · diff 落地
1. ✅ `chapter_diff_history` 表 + commit/list/rollback 后端
2. ✅ 段落级 diff 算法（按 `\n` LCS + 合并 replace） + DiffOverlay 覆盖层组件（红绿对照、段右侧 ✓/✗）
3. ✅ 顶部「全部接受 / 全部拒绝 / 应用并保存 / 退出」浮条
4. ✅ AI 输出消息加「**改正文**」「**插入光标**」两个按钮（"留 chat" 是默认行为）
5. ✅ 拒绝下拉（**直接放弃** / **说明理由** — modal 输入）
6. ⬜ 段级精细并发锁 — MVP 等价用「整个 Lexical 隐藏 + 覆盖层」实现（全章只读），段级锁留给阶段 7 打磨
7. ✅ diff 历史面板（章节标题旁 ⏱ 入口 → Drawer：列表 + 段落对比 + 回滚）

> **MVP 实现说明**：本期不做 Lexical 内嵌渲染，而是用全屏覆盖层显示段落对比。
> 优点：实现简单可控；中文段落上下并排对比天然清晰；不动 Lexical 内部状态。
> Lexical 内嵌 diff 节点不在该设计范围内。
>
> **回滚机制**：`POST /chapter-diff/by-id/{id}/rollback` 把指定历史的 `before_text` 写回 articles，
> 同时插入一条 `source='rollback_of:N'` 的新历史，所以**回滚也会被记录**，可继续往前回滚或再次回滚回去。

### 阶段 5 · Canvas 草稿区
1. ✅ `chapter_canvas` 表 + GET/PUT/DELETE 后端 + IPC
2. ✅ 右栏 `[📝 正文][💡 Canvas 草稿]` Tab 切换（Segmented，diff 激活时强制锁正文 Tab）
3. ✅ AI 输出按钮升级为 Dropdown.Button：主操作=改正文，下拉项=「改 Canvas」
4. ✅ CanvasView 顶部 banner + 「合并到正文」按钮：用 article.content vs canvas.content 启动 diff session，自动切回正文 Tab
5. ✅ Canvas autosave（同 articles 800ms 节流）+ 清空按钮（不影响正文）
6. ✅ AI「改 Canvas」自动切到 Canvas Tab；Canvas「合并到正文」自动切到正文 Tab

### 阶段 6 · 写作模式 + 内联 AI
1. ✅ 选中文字浮起 Inline Edit 工具条（润色 / 精简 / 扩写 / 自定义 → Popover AI 流式 → 替换选中）
2. ✅ 编辑器内 ghost text 续写（光标停留 800ms 触发 → 半透明虚影 → Tab 接受 / Esc 取消）
3. ✅ ⌘K 全局命令面板（主区域切换 / 章节跳转 / 视图切换 / 动作触发）
4. ✅ AI 浮岛形态（`mainPanel === 'editor'` 时 `ai-panel--compact`：隐藏 system prompt / session 栏，气泡紧凑）

> **Inline Edit 实现**：Lexical 暴露 `captureSelection()` 拿到 `{ text, restore, replace }` 快照；
> `SelectionChangePlugin` 上抛非空文本选区的 rect → `InlineEditLayer` 绘制浮起工具条。
> 点预设 → 调 `captureSelection()` 锁定选区 → 打开 Popover → 复用 `aiChatStream` 流式 → 接受时 `replace(newText)`。
> 快照机制保证了 Popover 抢焦后原选区仍可无痛恢复。
>
> **Ghost text 实现**：Lexical `IdleDetectPlugin` 在选区空闲 800ms 且前缀足够时回调 `onGhostIdle({ prefix, cursorRect })`；
> `GhostCompletion` 根据 trigger token 发起 `aiChatStream`（max_tokens=120 / temperature=0.5）→ 流式累加并在遇到 `\n` 时截断为单行；
> 以 `position:fixed + pointerEvents:none` 虚影覆盖在光标 rect.right；
> 键盘 capture 阶段拦截 `Tab`（接受→ `insertAtCursor`）与 `Esc`（取消）；任何 Lexical update 都会触发 `onGhostReset`。
> MVP 刻意保持单行，多行续写留给后续。Inline Edit / AiFloat 打开时因 `activeElement !== root` 天然不触发。
>
> **⌘K 命令集合**：命令定义在 Workspace 顶层（能直接 setState），跨组件动作通过 `window` 自定义事件派发（`editor-set-tab` / `editor-open-diff-history` / `editor-reformat` / `editor-copy-*`），
> EditorPanel 监听后执行。动态章节导航也作为命令项直接进入列表。
>
> **浮岛形态**：不改布局结构（仍占 22% 列宽），走 CSS modifier 路线：`ai-panel--compact` 隐藏 `.system-prompt-area` / `.session-tabs-bar`，压缩气泡与输入区字号和 padding，让写作主视图时 AI 变成侧边协作者而非主角。

### 阶段 7 · 打磨 ✅
1. ✅ 状态持久化
2. ✅ 快捷键体系
3. ✅ 切书状态隔离
4. ✅ 性能优化（Diff Web Worker）

> **持久化清单**：
> - 三栏布局 / 主区域 / 拖拽宽度 / 折叠状态 → `purrtypos_workspace_panel_state`（旧）
> - 笔记本各分组展开态 → `purrtypos_notebook_section_state`（旧，按 `CollapsibleSection.id`）
> - AI 上下文勾选（关联章节 / 大纲）→ `purrtypos_ai_associated_context`，**按 bookId 隔离**，切书自动切换
> - 当前活跃章节 → `purrtypos_active_chapter_by_book`，**按 bookId 隔离**，下次打开本书自动恢复到上次章节
> - 正文 / Canvas Tab → `purrtypos_editor_tab`（全局）
> - 模型偏好 / chatAgentMode / thinkingEnabled → `loadModelPrefs`（旧，按 bookId）

> **快捷键体系**（见命令面板 hint）：
> - `Ctrl/Cmd+K`：命令面板
> - `Ctrl/Cmd+Shift+1`：切到设定主区域（📓）
> - `Ctrl/Cmd+Shift+2`：切到导演主区域（🎬 AI）
> - `Ctrl/Cmd+Shift+3`：切到写作主区域（📝）
> - `Ctrl/Cmd+Shift+T`：正文 ↔ Canvas 切换（diff 审阅中会提示被锁）
> - `Ctrl/Cmd+Shift+H`：打开本章 diff 历史 Drawer
> 实现处：`Workspace/index.tsx` 全局 `keydown`；跨组件命令通过 `window` CustomEvent 解耦触发。

> **Diff Worker**：`src/Workspace/diff/diffWorker.ts` 是独立 module worker，`paragraphDiff.diffParagraphsAsync` 按阈值 8000 字符分流：
> - 短文本 → 主线程同步 `diffParagraphs`，立刻出结果
> - 长文本 → 懒初始化单例 worker，`postMessage` 请求 + id 匹配响应；`DiffContext.startDiff` 先放 `computing: true` 的骨架 session，DiffOverlay 显示 spinner「正在后台计算段落差异…」，完成后补齐 ops；「应用」按钮在 computing 期间禁用。5s 超时 fallback 同步。
> - Worker 失败或创建失败时全部 fallback 同步，保持功能可用性。

---
