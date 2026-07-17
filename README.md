# PurrTypos

**Electron 桌面写作应用** — 多书籍管理、大纲（XMind / Markdown / 思维导图）、章节正文（Lexical 富文本）、人物与小说背景，以及可配置多模型的 Writing Agent / 纯问答。

```
┌────────────────────┐    HTTP/SSE     ┌────────────────────────┐
│ Electron 渲染进程  │  ────────────►  │  Python 后端 (FastAPI) │
│  (Vite + React)    │  127.0.0.1:18321│  uvicorn + aiosqlite   │
└────────────────────┘                 └────────────────────────┘
        ▲  IPC                                   │
        │                                        ▼
┌────────────────────┐                   ┌──────────────────┐
│ Electron 主进程    │  spawn(python)    │   purrtypos.db   │
│ (main_python.js)   │  ───────────────► │   (SQLite WAL)   │
└────────────────────┘                   └──────────────────┘
```

## 环境要求

- **Node.js** 18+（推荐 20+），npm 8+
- **Python** 3.11+（Windows 安装时勾选 `py launcher`）
- 可选：**PyInstaller**（打包 Windows 安装包时由 `npm run build:win` 自动安装）

## 安装

```bash
# 1. 安装前端依赖
npm install

# 2. 安装后端依赖（路径相对仓库根目录）
pip install -r backend/requirements.txt
```

如 Electron 二进制下载失败（"Electron failed to install correctly"），可尝试：

```bash
node node_modules/electron/install.js
# 或配置 .npmrc 国内镜像后
npm rebuild electron
```

## 开发

```bash
npm run dev
```

会同时启动：

1. **Vite** 开发服务（`http://localhost:5173`，热更新）
2. **Electron 主进程**（`electron/main_python.js`），它会再 `spawn` 一个 Python 后端子进程：
   - 开发：直接调用本机 `python`/`py` 运行 `backend/main.py`
   - 打包：调用 PyInstaller 产物 `purrtypos-backend.exe`
3. 后端监听 `http://127.0.0.1:18321`，前端通过 `preload_python.js` 暴露的 `window.electronAPI` 走 fetch 调用 `/api/*`，AI 流式接口走 SSE。

主进程（`electron/`）和后端 Python 代码改动后需重启 `npm run dev`；渲染进程（`src/`）支持 HMR。

## 打包

```bash
# Windows：先打 PyInstaller，再 electron-builder
npm run build:win

# 仅前端打包（不包含 Python 后端）
npm run build
```

产物：
- 前端构建到 `dist/`
- Python 后端到 `backend/dist/purrtypos-backend/`
- 安装包到 `dist-electron/`（Windows NSIS / macOS DMG）

## AI 模式

工作台右侧 AI 面板提供两种工作流：

| 模式 | 适用 | 特征 |
|---|---|---|
| **Writing Agent** (`agent`) | 写作与项目任务 | 三层 Agent 执行规划、上下文预算、受限工具调用、人工审批和结果校验 |
| **纯问答** (`ask`) | 答疑、构思 | 不携带工具，单轮文本响应 |

模型接入提供两条路径：系统固定提供内置模型，设置页只允许配置其服务商凭据与运行参数，不能新增、复制或删除；代理、自建服务或目录外模型继续使用“高级自定义”。两种路径最终生成相同的 `AiModelConfig`，沿用同一套后端调用链。缺少 API Key 的内置模型仍显示在设置页，但不会进入对话模型列表。

内置模型采用 profile 分层适配：`src/models/profiles/` 保存前端目录能力与配置迁移，`backend/infrastructure/models/profiles/` 保存请求参数和响应规范化差异；OpenAI / Anthropic SDK、流式生命周期、工具调用和错误处理仍由公共协议适配器负责。内置配置通过 `model_profile` 命中对应 profile，高级自定义不携带该字段并回退到 generic profile。

Kimi K3 通过独立 profile 接入 `kimi-k3`，使用 1M 上下文和当前服务端支持的 Max 思考模式；Agent 工具续轮会回传模型的 `reasoning_content`，避免丢失 K3 的思考历史。

### 工具系统（Skills）

`backend/skills/<name>/SKILL.md` 是工具定义的单一来源，文件由：
1. YAML frontmatter（`name` / `description`）
2. 正文中的 ` ```json ` 代码块（OpenAI 函数调用的 `parameters` JSON Schema）

后端启动时由 `WritingSkillCatalog` 扫描整个 `skills/` 目录，并由 Writing Domain 校验 Schema、Policy 与 Infrastructure Handler 一致。新增工具必须同时提供 `SKILL.md`、业务 Policy/规划约束和具体 Handler；任一缺失都会在装配时失败关闭。

## 项目结构

```
PurrTypos/
├── electron/                    # Electron 主进程
│   ├── main_python.js           # 窗口、IPC、Python 后端进程的生命周期管理
│   ├── preload_python.js        # 暴露 window.electronAPI（HTTP + SSE 透传）
│   └── ...
├── backend/                     # Python 后端（FastAPI + aiosqlite）
│   ├── main.py                  # FastAPI 入口，CORS、路由装载、生命周期
│   ├── requirements.txt
│   ├── purrtypos-backend.spec   # PyInstaller 打包配置
│   ├── routers/                 # HTTP/SSE 路由层
│   │   ├── ai.py                # /ai/chat/stream（SSE）、/ai/title、/ai/models
│   │   ├── books.py / outlines.py / chapters.py / characters.py / ...
│   │   └── conversations.py / sessions.py / settings.py / ...
│   ├── agent_core/              # 业务无关的规划、状态机、模型轮次、工具与审批内核
│   ├── application/             # 唯一 Composition Root、请求/SSE 映射和应用用例
│   ├── domains/writing/         # Writing 业务规则、Planning Policy、上下文与工具契约
│   ├── infrastructure/          # Provider、SQLite Repository、技能目录和 Writing Handler
│   ├── services/                # 非 Agent 架构的长期记忆应用服务
│   ├── database/
│   │   ├── connection.py        # aiosqlite 单连接 + WAL + 事务管理
│   │   ├── schema.py            # 建表 / 增量迁移
│   │   └── crud/                # 各表 CRUD（books/outlines/chapters/...）
│   ├── utils/                   # 通用纯函数与异步流辅助
│   ├── schemas/                 # Pydantic 请求体
│   └── skills/                  # 工具定义（每个工具一个目录 + SKILL.md）
├── src/                         # 渲染进程（React + TypeScript）
│   ├── App.tsx                  # 路由：首页 / 书架 / 工作台
│   ├── HomePage/ BookshelfPage/ SettingsPage/
│   ├── Workspace/
│   │   ├── OutlinePanel/        # 大纲（Tiptap）、人物、小说背景
│   │   ├── EditorPanel/         # 章节正文（Lexical）、内联 AI、Ghost 补全
│   │   └── AiPanel/             # AI 对话、Agent Run、工具审批、记忆/收藏管理
│   └── types.ts                 # 前后端共享 IPC 类型契约
├── scripts/                     # 构建辅助脚本
└── package.json
```

## 数据存储

- **位置**：用户数据目录下的 `purrtypos.db`（Windows：`%APPDATA%\purrtypos\purrtypos.db`，macOS：`~/Library/Application Support/purrtypos/purrtypos.db`，由 Electron `app.getPath('userData')` 决定，并通过 `PURRTYPOS_DATA_DIR` 环境变量传给 Python 后端）。
- **驱动**：`aiosqlite`（异步 SQLite）+ WAL 日志模式，单连接复用，写锁 `busy_timeout=5000ms`。多步写操作通过 `db.transaction()` 上下文管理器原子化（如 `delete_book`）。
- **主要表**：`books`、`outlines`、`outline_chapters`、`articles`、`characters`、`story_background` / `story_background_attachments`、`ai_sessions` / `ai_conversations`、`ai_favorites`、`ai_memories` / `ai_foreshadowing`、`memory_items` / `memory_links`、`book_style`、`outline_history`、`chapter_diff`、`prompt_templates`、`settings`。建表与迁移在 `backend/database/schema.py`。
- **导入 / 导出**：设置面板 → 数据 → 数据库导出/导入，覆盖式导入会替换当前所有数据，请先备份。

## 长期记忆

- `memory_items` 是统一长期记忆池，覆盖设定、剧情事实、人物状态、世界观、伏笔、风格和阶段总结；`memory_links` 保存冲突、替代、支持、相关等关系。
- 默认使用 SQLite FTS5 本地召回与规则沉淀，不依赖外部服务；AI 接受的 diff / inline edit 会生成 `pending` 候选，用户明确“记住”的内容和手动保存的设定会写入 `active`。
- 记忆中心里的“高级智能记忆”开关默认关闭。开启后，AI 来源改动会使用已配置的第一个可用模型提炼更精细的 `pending` 候选，并尝试生成冲突/替代/伏笔等关系；模型不可用或输出无效时自动回退到本地规则候选。

## 健康检查

```
GET http://127.0.0.1:18321/health
→ { "status": "ok", "db": "up" }      # 一切正常
→ { "status": "degraded", "db": "down" }  # 后端进程在但 DB 连接挂了
```

## 异常处理（简要）

- **XMind 解析失败**：界面提示原因。
- **AI 请求失败**：对话区域展示错误信息；SSE 中断不会污染历史。
- **Python 后端崩溃**：Electron 主进程会在控制台打印日志；窗口仍可见，但调用任何 `/api/*` 都会失败 — 重启应用即可。
- **数据库**：初始化或读写失败时主进程控制台会有日志；导入数据库前请确认备份。
