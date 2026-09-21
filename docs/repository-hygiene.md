# 仓库文件与忽略规则

按文件用途判断是否提交，不按目录名称一概排除。

| 路径 | 提交策略 | 原因 |
| --- | --- | --- |
| `src/`、`backend/`、`electron/`、`public/`、`scripts/` | 保留源码、测试和原始资源 | 开发、验证与分发需要 |
| `build/icon.js`、`build/icon.test.js` | 保留 | 图标生成脚本与测试，`build/` 不是纯产物目录 |
| `build/icon.png`、`build/icon.ico` | 忽略 | 从 `public/PurrTypos.png` 生成；Electron 构建命令会先执行 `node build/icon.js` |
| `.github/workflows/` | 保留 | GitHub 自动测试和 Windows 后端打包配置 |
| `docs/`（含 `docs/superpowers/`） | 保留共享协议、设计、计划、验收记录和验证材料 | README 和验证脚本有直接引用；历史设计用于追溯，不代表当前实现 |
| `.cursor/rules/`、`.vscode/settings.json` | 保留项目级配置 | 当前为编码约定和通用编辑器设置；过时规则应单独修订，不能靠忽略隐藏 |
| `.superpowers/`、`sketches/` | 仅留本地 | 工具任务进度、过程报告和一次性 HTML 原型；需长期共享的结论应整理进 `docs/` |
| `docs/local/` | 仅留本地 | 个人笔记及包含本机数据库标识的审计快照 |
| `dist*`、`build-resources/`、`backend/build/`、依赖、缓存、虚拟环境 | 忽略 | 可重建的本机产物 |
| `.env*`、`local-backups/`、`*.purrbackup`、`purrtypos.db` 及 WAL/SHM | 忽略 | 本机凭据、业务数据和备份；`.env.example` 允许提交 |

`.gitignore` 不会自动取消已经跟踪的文件。清理使用指定路径的 `git rm --cached`，保留本地文件；提交并推送后，远端当前分支才会移除它们。历史提交仍保留旧文件。

不要全局忽略 `*.json`、`*.md`、所有图片或整个 `build/`、`docs/`，否则会隐藏源码资源、协议与测试材料。个人笔记、临时输出应按明确路径单独忽略。Git 忽略规则也不是分发包过滤规则，分发范围由打包脚本和配置单独决定。
