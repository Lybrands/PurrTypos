# PurrTypos

**本地优先写作应用** — 支持 Electron 桌面窗口和本地浏览器两种运行方式，提供多书籍管理、大纲（XMind / Markdown）、章节正文（Lexical 富文本）、人物与小说背景、小说源管理与小说分析、剧本工作台和写作技法，并通过可配置多模型的 Agent（小说写作 / 小说分析 / 剧本）与纯问答辅助创作。

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

- **Node.js** 20.19+ 或 22.12+（Vite 8 的最低要求），npm 8+
- **Python** 3.11+（Windows 安装时勾选 `py launcher`）
- 可选：**PyInstaller**（打包 Windows 安装包时由 `npm run build:win` 自动安装）

## 安装

```bash
# 1. 安装前端依赖
npm install

# 2. 从本仓库根目录安装后端依赖
pip install -r backend/requirements.txt
```

`backend/requirements.txt` 由三部分组成：

- `requirements-purra.txt` — 按相对路径引用 `backend/vendor/purra-1.1.1/` 内的四个
  PurrA 1.1.1 候选 wheel（`purra`、`purra_openai`、`purra_anthropic`、`purra_mem0`），
  行内注释记录 SHA-256；**必须在仓库根目录执行 pip**。
- `requirements-runtime.txt` — FastAPI、uvicorn、aiosqlite 等运行时依赖。
- 其余为开发 / 测试依赖（setuptools、pytest），PyInstaller 打包不会带入运行时。

PurrA 源码基线与 wheel 校验和集中记录在 `backend/purra-candidate.json`。开发、CI 和
分发打包使用同一份依赖清单，无需同级 PurrA 源码目录。

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

## 测试与检查

```bash
npm run check        # purr-components 检查 + typecheck + 前端单元测试 + 后端测试
npm run test:unit    # 前端单元测试（node:test）
npm run test:backend # 后端 pytest 套件（backend/tests/）
npm run typecheck    # tsc --noEmit
```

专项验收（独立运行，耗时通常在分钟级）：

```bash
# Electron 端到端验收（三个产品 Agent 各一条）
npm run acceptance:writing:electron
npm run acceptance:novel-analysis:electron
npm run acceptance:screenplay:electron

# 后端专项
npm run test:screenplay-acceptance
npm run test:model-contracts
npm run check:agent-stability
```

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
| **Agent** (`agent`) | 写作、分析与项目任务 | PurrA Run、上下文预算、受限工具调用、写入提案和结果校验 |
| **纯问答** (`ask`) | 答疑、构思 | 不携带工具，单轮文本响应 |

Agent 分为三个产品形态：小说写作（工作台 AI 面板）、小说分析（小说源页面）和
剧本（剧本工作台），分别由 `backend/agents/writing`、`backend/agents/novel_analysis`
和 `backend/agents/screenplay` 承载。

模型接入提供两条路径：系统固定提供五家内置服务商（智谱 GLM、DeepSeek、Kimi/Moonshot、MiniMax、小米 MiMo），接入端点与协议由系统维护，**模型名称由用户在设置页填写**，思考/非思考全部放开、由用户按所填模型声明；代理、自建服务或目录外服务商继续使用"高级自定义"。两种路径最终生成相同的 `AiModelConfig`，沿用同一套后端调用链。缺少 API Key 或模型名的内置条目仍显示在设置页，但不会进入对话模型列表。

内置服务商采用 profile 分层适配：`src/models/profiles/` 保存前端目录与服务商接入信息，`backend/infrastructure/models/profiles/` 保存请求参数和响应规范化差异（服务商级 profile 只按接入端点匹配，不校验模型名）；OpenAI / Anthropic SDK、流式生命周期、工具调用和错误处理由公共协议适配器负责。每家登记主推模型的能力上限作为默认值，用户可在配置中按实际模型覆盖。模型能力描述符由 `scripts/generate-model-descriptors.py` 生成到 `src/models/descriptors.generated.ts`，不要手工编辑。内置配置通过 `model_profile` 命中对应服务商 profile，高级自定义不携带该字段并回退到 generic profile；旧模型级 profile id（如 `zai:glm-5.3-flash`）已冻结，仅用于解析存量配置与历史请求。

### Agent 工具系统

三个产品 Agent 的工具合同由各自 replacement 目录中的代码化 catalog 唯一定义，
工具 schema、授权范围、执行 handler 与结果校验在同一实现版本内组合，不扫描
`backend/skills`，也不存在旧 SkillCatalog 回退路径。Agent 目录的依赖方向与
实现版本路由规则见 [`backend/agents/README.md`](backend/agents/README.md)。

## 项目结构

```
PurrTypos/
├── electron/                    # Electron 主进程
│   ├── main_python.js           # 窗口、IPC、Python 后端进程的生命周期管理
│   ├── backend_process.js       # 后端进程管理（PURRTYPOS_DATA_DIR 等环境变量）
│   ├── preload_python.js        # 仅暴露 window.purrDesktop 原生能力
│   └── ...
├── src/                         # 渲染进程（React + TypeScript）
│   ├── App.tsx                  # 路由：首页 / 书架 / 工作台 / 小说源 / 剧本 / 写作技法
│   ├── HomePage/ BookshelfPage/ SettingsPage/
│   ├── NovelSourcesPage/        # 小说源管理与小说分析 Agent
│   ├── ScreenplayAgentPage/     # 剧本工作台与剧本 Agent
│   ├── WritingMethodsPage/      # 写作技法
│   ├── Workspace/
│   │   ├── OutlinePanel/        # 大纲（Tiptap）、人物、小说背景
│   │   ├── EditorPanel/         # 章节正文（Lexical）、内联 AI、Ghost 补全
│   │   ├── AiPanel/             # AI 对话、Agent Run、工具审批、记忆/收藏管理
│   │   ├── DashboardPanel/ DirectorNotebook/ KnowledgePanel/ SettingPanel/
│   │   └── ...
│   ├── components/AgentConversation/  # 三个 Agent 共用的对话界面组件
│   ├── agent-runtime/           # SSE 事件流、会话恢复等 Agent 运行时逻辑
│   ├── models/                  # 内置模型目录、profile 与生成的能力描述符
│   ├── purr-components/         # 共享 UI 组件
│   ├── services/                # 与运行时无关的领域服务、HTTP 与 AI SSE
│   ├── platform/                # Electron / Browser 文件和系统能力适配
│   └── types.ts                 # 前后端共享 IPC 类型契约
├── backend/                     # Python 后端（FastAPI + aiosqlite）
│   ├── main.py                  # FastAPI 入口，CORS、路由装载、生命周期
│   ├── requirements.txt         # = purra + runtime + 开发/测试依赖
│   ├── requirements-purra.txt   # vendor 内 PurrA 1.1.1 本地 wheel（根目录执行 pip）
│   ├── requirements-runtime.txt # FastAPI / uvicorn / aiosqlite 等运行时依赖
│   ├── vendor/purra-1.1.1/      # PurrA 1.1.1 候选 wheel（core/openai/anthropic/mem0）
│   ├── purra-candidate.json     # PurrA 源码基线与 wheel SHA-256 记录
│   ├── purrtypos-backend.spec   # PyInstaller 打包配置
│   ├── routers/                 # HTTP/SSE 路由层
│   │   ├── ai.py                # /ai/chat/stream（SSE）、/ai/title、/ai/models
│   │   ├── books.py / outlines.py / chapters.py / articles.py / characters.py / ...
│   │   ├── novel_sources.py / novel_knowledge.py   # 小说源管理与小说分析
│   │   ├── screenplay_v2.py / screenplay_conversations.py  # 剧本工作台
│   │   ├── writing_techniques.py / continuations.py / dashboard.py / ...
│   │   └── conversations.py / sessions.py / settings.py / ...
│   ├── application/             # 唯一 Composition Root、请求/SSE 映射和应用用例
│   ├── agents/                  # 三个 replacement Agent 与共享宿主适配
│   │   ├── shared/              # 公共 PurrA Run、取消、恢复和呈现边界
│   │   ├── writing/             # 小说创作 profile、上下文、工具与策略
│   │   ├── novel_analysis/      # 小说分析 profile、任务单元与工具
│   │   └── screenplay/          # 剧本 profile、阶段任务、Artifact 与发布
│   ├── domains/                 # 与 Agent 执行器解耦的产品业务规则
│   ├── infrastructure/          # Provider、模型 profile、SQLite Repository
│   ├── services/                # 业务源策略与组件投递服务
│   ├── database/
│   │   ├── connection.py        # aiosqlite 单连接 + WAL + 事务管理
│   │   ├── schema.py            # 建表 / 增量迁移（68 张表）
│   │   └── crud/                # 各表 CRUD（outlines/chapters/articles/characters/...）
│   ├── tests/                   # pytest 套件（npm run test:backend）
│   ├── utils/                   # 通用纯函数与异步流辅助
│   └── schemas/                 # Pydantic 请求体
├── scripts/                     # 构建、验收与校验辅助脚本
└── package.json
```

## 数据存储

- **位置**：用户数据目录下的 `purrtypos.db`（Windows：`%APPDATA%\purrtypos\purrtypos.db`，macOS：`~/Library/Application Support/purrtypos/purrtypos.db`，由 Electron `app.getPath('userData')` 决定，并通过 `PURRTYPOS_DATA_DIR` 环境变量传给 Python 后端）。
- **驱动**：`aiosqlite`（异步 SQLite）+ WAL 日志模式，单连接复用，写锁 `busy_timeout=5000ms`。多步写操作通过 `db.transaction()` 上下文管理器原子化（如 `delete_book`）。
- **主要表**：`books`、`outlines` / `outline_chapters`、`articles`、`characters`（含 `character_history` / `character_options`）、`story_background` 及附件与历史表、`setting_entities` 及历史表、`chapter_canvas`、`ai_sessions` / `ai_conversations`（含摘要）、`ai_agent_runs` 及运行事件 / 审批 / 工具回执 / Artifact 相关表、`ai_provider_health` 及容量与租约表、`ai_favorites`、`ai_prompt_templates`、`ai_memories` / `ai_foreshadowing`、`memory_source_heads` / `memory_source_deliveries`、`story_memory_*` 系列表、`screenplay_projects` 及剧本回执表、`outline_history`、`chapter_diff_history`、`book_word_stats`、`settings`。完整清单见 `backend/database/schema.py`。
- **备份 / 恢复**：设置面板 → 数据 → 完整备份/恢复。`.purrbackup` 同时包含业务数据库与 `memory-component-v1`，恢复会成套替换两者；API 密钥不写入备份。

## 长期记忆

- 通用长期记忆、版本、关系、评审、Embedding 和向量检索由当前锁定的 PurrA 1.1.1 候选包 `purra-mem0` 组件持有，数据位于用户数据目录的 `memory-component-v1/`；PurrTypos 不维护平行的长期记忆表或召回实现。
- PurrTypos 只保留业务源到组件的投递策略与持久化 outbox（`memory_source_heads` / `memory_source_deliveries`）。用户保存与业务提交先在 SQLite 中完成，组件投递失败会如实返回并由恢复流程重试。
- Story Memory 仍是独立的章节证据状态账本，用于版本、来源失效和审阅；它与可编辑的通用长期记忆具有不同生命周期。模型输入由两者共同组装，并在调用 Provider 前校验版本化 evidence receipt。

## Agent 规范

小说写作、小说分析和剧本 Agent 的公共输入、流式输出与恢复遵循[共享对话与执行规范](docs/design/shared-agent-conversation-contract.md)。三个 Agent 的 PurrA 原生重建计划见 [2026-09-12 重建计划](docs/design/2026-09-12-three-agent-purra-native-rebuild-plan.md)。各领域保留材料选择、工具、审批和交付物规则。

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
