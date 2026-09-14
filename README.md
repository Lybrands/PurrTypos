# PurrTypos

**本地优先写作应用** — 支持 Electron 桌面窗口和本地浏览器两种运行方式，提供多书籍管理、大纲（XMind / Markdown / 思维导图）、章节正文（Lexical 富文本）、人物与小说背景，以及可配置多模型的 Writing Agent / 纯问答。

```
┌────────────────────┐    HTTP/SSE     ┌────────────────────────┐
│ React 前端          │  ────────────►  │  Python 后端 (FastAPI) │
│  (Vite + React)    │  127.0.0.1:18321│  uvicorn + aiosqlite   │
└────────────────────┘                 └────────────────────────┘
        ▲                                        │
        │ Electron IPC（仅文件/系统能力）          ▼
┌────────────────────┐                   ┌──────────────────┐
│ Electron 或浏览器   │                   │   purrtypos.db   │
│ 两种运行时          │                   │   (SQLite WAL)   │
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

# 2. 从本仓库根目录安装后端依赖
pip install -r backend/requirements.txt
```

PurrA Core、OpenAI、Anthropic 和 Mem0 集成通过
`backend/requirements-purra.txt` 固定引用仓库内的 PurrA 1.0.0 候选 wheel，
其来源与 SHA-256 记录在 `backend/purra-candidate.json`；Mem0 保留 `managed`
扩展依赖。开发、CI 和分发打包使用同一份依赖清单，无需同级 PurrA 源码目录。

如果已有环境安装过同版本的本地 wheel 或可编辑包，先替换这四个包，再检查完整依赖：

```bash
python -m pip install --force-reinstall --no-deps -r backend/requirements-purra.txt
python -m pip install -r backend/requirements.txt
python -m pip check
```

更新后重启后端进程，并运行 `python scripts/verify-purra-candidate.py` 核验实际安装内容。
候选 wheel 通过本地验收不等同于已发布到公共仓库。

如 Electron 二进制下载失败（"Electron failed to install correctly"），可尝试：

```bash
node node_modules/electron/install.js
# 或配置 .npmrc 国内镜像后
npm rebuild electron
```

## 开发

```bash
# 默认：在浏览器中启动
npm run dev

# 显式启动浏览器版
npm run dev:web

# Electron 桌面窗口
npm run dev:electron
```

两种方式都使用相同的 `src/services/` HTTP/SSE 服务层和 FastAPI 后端。Electron
的 `preload_python.js` 只暴露文件选择、保存对话框和打开本地路径等系统能力；
浏览器使用上传、下载和预览完成对应操作。`npm run dev` 默认等同于
`npm run dev:web`。

后端监听 `http://127.0.0.1:18321`。浏览器开发页面由 Vite 提供，生产版页面由
FastAPI 在同一端口托管。

## 打包

```bash
# Electron 安装包（macOS/当前平台）
npm run build:electron

# Electron Windows 安装包（先构建 PyInstaller 后端）
npm run build:win

# 本地浏览器发行包
npm run build:web

# Windows 本地浏览器发行包（包含 PyInstaller 后端）
npm run build:web:win
```

产物：
- 前端构建到 `dist/`
- Python 后端到 `backend/dist/purrtypos-backend/`
- 安装包到 `dist-electron/`（Windows NSIS / macOS DMG）
- 本地浏览器发行目录到 `dist-web/`，通过 `start-web.cmd` 或 `start-web.sh` 启动

## AI 模式

工作台右侧 AI 面板提供两种工作流：

| 模式 | 适用 | 特征 |
|---|---|---|
| **Writing Agent** (`agent`) | 写作与项目任务 | PurrA Run、上下文预算、受限工具调用、写入提案和结果校验 |
| **纯问答** (`ask`) | 答疑、构思 | 不携带工具，单轮文本响应 |

模型接入提供两条路径：系统固定提供内置模型，设置页只允许配置其服务商凭据与运行参数，不能新增、复制或删除；代理、自建服务或目录外模型继续使用“高级自定义”。两种路径最终生成相同的 `AiModelConfig`，沿用同一套后端调用链。缺少 API Key 的内置模型仍显示在设置页，但不会进入对话模型列表。

内置模型采用 profile 分层适配：`src/models/profiles/` 保存前端目录能力与配置迁移，`backend/infrastructure/models/profiles/` 保存请求参数和响应规范化差异；OpenAI / Anthropic SDK、流式生命周期、工具调用和错误处理仍由公共协议适配器负责。内置配置通过 `model_profile` 命中对应 profile，高级自定义不携带该字段并回退到 generic profile。

Kimi K3 通过独立 profile 接入 `kimi-k3`，使用 1M 上下文和当前服务端支持的 Max 思考模式；Agent 工具续轮会回传模型的 `reasoning_content`，避免丢失 K3 的思考历史。

### Agent 工具系统

三个产品 Agent 的工具合同由各自 replacement 目录中的代码化 catalog 唯一定义：
`backend/agents/writing`、`backend/agents/novel_analysis` 和
`backend/agents/screenplay`。工具 schema、授权范围、执行 handler 与结果校验在同一实现
版本内组合，不再启动扫描 `backend/skills`，也不存在旧 Writing SkillCatalog 回退路径。

## 项目结构

```
PurrTypos/
├── electron/                    # Electron 主进程
│   ├── main_python.js           # 窗口、IPC、Python 后端进程的生命周期管理
│   ├── preload_python.js        # 仅暴露 window.purrDesktop 原生能力
│   └── ...
├── src/services/                # 与运行时无关的领域服务、HTTP 与 AI SSE
├── src/platform/                # Electron / Browser 文件和系统能力适配
├── backend/                     # Python 后端（FastAPI + aiosqlite）
│   ├── main.py                  # FastAPI 入口，CORS、路由装载、生命周期
│   ├── requirements.txt
│   ├── purrtypos-backend.spec   # PyInstaller 打包配置
│   ├── routers/                 # HTTP/SSE 路由层
│   │   ├── ai.py                # /ai/chat/stream（SSE）、/ai/title、/ai/models
│   │   ├── books.py / outlines.py / chapters.py / characters.py / ...
│   │   └── conversations.py / sessions.py / settings.py / ...
│   ├── application/             # 唯一 Composition Root、请求/SSE 映射和应用用例
│   ├── agents/                  # 三个 replacement Agent 与共享宿主适配
│   │   ├── shared/              # 公共 Run、取消、恢复和呈现边界
│   │   ├── writing/             # 小说创作 profile、上下文、工具与策略
│   │   ├── novel_analysis/      # 小说分析 profile、任务单元与工具
│   │   └── screenplay/          # 剧本 profile、阶段任务、Artifact 与发布
│   ├── domains/                 # 与 Agent 执行器解耦的产品业务规则
│   ├── infrastructure/          # Provider、SQLite Repository 和宿主基础设施
│   ├── services/                # 业务源策略与组件投递服务
│   ├── database/
│   │   ├── connection.py        # aiosqlite 单连接 + WAL + 事务管理
│   │   ├── schema.py            # 建表 / 增量迁移
│   │   └── crud/                # 各表 CRUD（books/outlines/chapters/...）
│   ├── utils/                   # 通用纯函数与异步流辅助
│   └── schemas/                 # Pydantic 请求体
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
- **主要表**：`books`、`outlines`、`outline_chapters`、`articles`、`characters`、`story_background` / `story_background_attachments`、`ai_sessions` / `ai_conversations`、`ai_favorites`、`ai_memories` / `ai_foreshadowing`、`story_memory_records` / `story_memory_deltas`、`memory_source_heads` / `memory_source_deliveries`、`book_style`、`outline_history`、`chapter_diff`、`prompt_templates`、`settings`。建表与迁移在 `backend/database/schema.py`。
- **备份 / 恢复**：设置面板 → 数据 → 完整备份/恢复。`.purrbackup` 同时包含业务数据库与 `memory-component-v1`，恢复会成套替换两者；API 密钥不写入备份。

## 长期记忆

- 通用长期记忆、版本、关系、评审、Embedding 和向量检索由当前锁定的 PurrA 1.0.0 候选包 `purra-mem0` 组件持有，数据位于用户数据目录的 `memory-component-v1/`；PurrTypos 不再维护平行的长期记忆表或召回实现。
- PurrTypos 只保留业务源到组件的投递策略与持久化 outbox（`memory_source_heads` / `memory_source_deliveries`）。用户保存与业务提交先在 SQLite 中完成，组件投递失败会如实返回并由恢复流程重试。
- Story Memory 仍是独立的章节证据状态账本，用于版本、来源失效和审阅；它与可编辑的通用长期记忆具有不同生命周期。模型输入由两者共同组装，并在调用 Provider 前校验版本化 evidence receipt。

## Agent 规范

小说写作、小说分析和剧本 Agent 的公共输入、流式输出与恢复遵循[共享对话与执行规范](docs/design/shared-agent-conversation-contract.md)。各领域保留材料选择、工具、审批和交付物规则。

模型配置解析、Provider 参数核验与后台调用已使用公共边界，契约和验收记录见[模型请求公共入口与适配规范](docs/design/2026-09-06-model-request-boundary-refactor.md)。蒸馏质量验证单独进行。

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
- **数据存储**：初始化或读写失败时主进程控制台会有日志；恢复完整备份前请确认已保留当前副本。
