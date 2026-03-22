# PurrTypos - 写作

Vite + React + Electron 桌面应用，支持 XMind 大纲、文档编辑与可配置模型 AI 对话。

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

或使用国内镜像（项目 `.npmrc` 已配置 npmmirror）重新安装：

```bash
npm rebuild electron
```

## 开发

```bash
npm run dev
```

会同时启动：

1. **Vite** 开发服务（http://localhost:5173，热更新）
2. **Electron** 窗口（等待 Vite 就绪后自动打开）

主进程修改后需重启 `npm run dev`；渲染进程（React）支持热更新。

## 打包

```bash
npm run build
```

流程：先执行 `vite build` 构建前端到 `dist/`，再执行 `electron-builder` 打包成安装包，输出在 `dist-electron/`。

- Windows：生成 NSIS 安装程序
- macOS：生成 .dmg

## 使用说明

1. **顶栏**：点击「API Key」配置可用模型（Provider、API Key、Base URL、模型名），否则 AI 功能不可用。
2. **左侧大纲**：点击「打开 .xmind 文件」选择 XMind 文件，解析后显示章节大纲；可切换标签「章节大纲 / 人物关系 / 小说背景」（后两者 MVP 为静态占位）；点击章节可标记进度并在右侧编辑。
3. **右侧编辑区**：选择章节后在此编辑，内容自动保存到 SQLite；输入 `\` 唤起悬浮 AI 输入框，输入需求后调用当前所选模型，结果展示在悬浮框内（不自动插入正文）。
4. **右侧 AI 对话**：输入提示词发送，与当前章节绑定的对话会保存到本地；可点击 🔧 设置系统提示词、🗑️ 清空当前对话。
5. **布局**：中间分割线可拖拽调整左右比例；各面板标题栏有全屏按钮，退出后恢复比例。

## 项目结构

```
EsayWrite/
├── electron/           # Electron 主进程
│   ├── main.js         # 入口、窗口、IPC
│   ├── preload.js      # 预加载脚本（暴露 API）
│   └── database.js     # SQLite 封装
├── src/
│   ├── components/
│   │   ├── OutlinePanel/   # 左侧大纲
│   │   ├── EditorPanel/    # 右侧编辑区
│   │   └── AiPanel/        # 右侧 AI 对话
│   ├── App.jsx
│   ├── App.css
│   ├── main.jsx
│   └── index.css
├── index.html
├── vite.config.js
└── package.json
```

## 数据存储

- SQLite 数据库文件位于系统用户数据目录（如 Windows `%APPDATA%/purrtypos/purrtypos.db`）。
- 表：`outlines`、`outline_chapters`、`articles`、`ai_conversations`，详见 `electron/database.js`。

## 异常处理

- XMind 解析失败：弹窗/提示「解析失败」及原因。
- AI 接口失败：对话区展示「请求失败：xxx」。
- 数据库初始化失败：主进程控制台输出错误，渲染进程可做降级提示。
