# PurrTypos

Vite + React + TypeScript + Electron 桌面写作应用：多书籍管理、XMind/大纲、章节正文编辑、人物与小说背景，以及可配置多模型的 AI 对话（含 Agent 工具调用与本地会话存储）。

## 环境要求

- Node.js 18+（推荐 20+）
- npm 8+

## 安装

```bash
npm install
```

若 Electron 启动报错「Electron failed to install correctly」，说明二进制未下载完整，可执行：

```bash
node node_modules/electron/install.js
```

或使用国内镜像（项目 `.npmrc` 可配置 npmmirror）后重装：

```bash
npm rebuild electron
```

## 开发

```bash
npm run dev
```

会同时启动：

1. **Vite** 开发服务（`http://localhost:5173`，热更新）
2. **Electron** 窗口（等待 Vite 就绪后加载页面）

主进程（`electron/`）修改后需重启 `npm run dev`；渲染进程（`src/`）支持热更新。

## 打包

```bash
npm run build
```

流程：生成图标 → `vite build` 输出到 `dist/` → `electron-builder` 生成安装包，产物在 `dist-electron/`。

- Windows：NSIS 安装程序  
- macOS：`.dmg`

## 使用说明

### 导航

1. **首页**：进入书架或打开设置。  
2. **书架**：创建/重命名/删除书籍，可选择是否启用分卷；可从多本书导出 Markdown/文本（支持 ZIP）。  
3. **写作工作台**：选定书籍后进入三栏布局——左侧大纲、中间编辑、右侧 AI。

### 设置（首页或工作台可打开）

- **通用**：主题、字体等。  
- **AI 配置**：系统提示词（保存后写入本地库，对话会携带）；可选「大纲章节同步」等选项。  
- **模型配置**：添加多个模型（OpenAI / Anthropic 兼容接口）、API Key、Base URL、Temperature、thinking 相关选项等；未配置有效模型时 AI 不可用。  
- **数据**：数据库导出 / 导入（覆盖当前数据，导入后通常会刷新页面）。

### 工作台

- **左侧大纲**：支持打开 `.xmind`、思维导图视图、Markdown 大纲等（与书籍/分卷/章节关联）；标签内可切换 **章节大纲 / 人物 / 小说背景**。  
- **人物**：新建、编辑、删除；字段含名称、性别、年龄、外貌、背景、小传、标签、**备注** 等；性格/标签候选可在「选项配置」中维护。  
- **小说背景**：书籍级背景正文与附件管理。  
- **中间编辑区**：当前章节正文（Lexical），自动保存到本地数据库。  
- **右侧 AI**：选择模型、开关 Agent、会话历史、收藏、记忆等；对话按书籍/会话持久化。分割线可拖拽；各面板支持全屏。

## 项目结构

```
PurrTypos/
├── electron/              # Electron 主进程
│   ├── main.js            # 窗口、IPC、与渲染进程通信
│   ├── preload.js         # 预加载，暴露 window.electronAPI
│   ├── database.js        # SQLite（sql.js）数据访问
│   ├── openaiChat.js      # 聊天流式请求等
│   ├── toolExecutor.js    # Agent 工具执行
│   └── toolRouter.js      # 工具路由
├── src/
│   ├── App.tsx            # 页面路由：首页 / 书架 / 工作台 + 设置浮层
│   ├── main.tsx           # React 入口，ConfigProvider + antd App
│   ├── hooks/             # 如 useAntdApp（与主题一致的 message 等）
│   ├── HomePage/
│   ├── BookshelfPage/
│   ├── SettingsPage/      # 设置（模型、系统提示词、数据）
│   ├── Workspace/         # 写作工作台
│   │   ├── OutlinePanel/  # 大纲、人物 CharacterTab、小说背景 StoryBackgroundTab
│   │   ├── EditorPanel/   # 章节列表与 Lexical 编辑
│   │   └── AiPanel/       # AI 对话与相关组件
│   ├── components/
│   └── contexts/          # 主题、字号等
├── main.js                # 打包入口，转调 electron/main.js
├── index.html
├── vite.config.js
└── package.json
```

## 数据存储

- 使用 **sql.js** 将 SQLite 数据库持久化到用户数据目录下的 **`purrtypos.db`**（例如 Windows：`%APPDATA%\purrtypos\purrtypos.db`，具体以 Electron `app.getPath('userData')` 为准）。  
- 主要数据表包括：`books`、`outlines`、`outline_chapters`、`articles`、人物 `characters`、小说背景 `story_background`、AI 会话 `ai_sessions` / `ai_conversations`、收藏 `ai_favorites`、记忆 `ai_memories`、应用设置 `settings` 等。表结构以 `electron/database.js` 为准。

## 异常处理（简要）

- **XMind 解析失败**：界面提示原因。  
- **AI 请求失败**：对话区域展示错误信息。  
- **数据库**：初始化或读写失败时主进程控制台会有日志；导入数据库前请确认备份。
