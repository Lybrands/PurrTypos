# mem0 长期记忆配置说明

项目已集成 [mem0](https://github.com/mem0ai/mem0) 作为长期记忆层，支持**四层记忆**（全局/大纲/人物/章节）和**伏笔记忆**的语义检索与本地持久化。

## 存储位置

- 向量库：`userData/mem0/vector_store.db`（SQLite，本地文件）
- 历史记录：`userData/mem0/history.db`

## Embedder 配置

mem0 需要 Embedding 模型将文本转为向量。支持两种方式：

### 1. Ollama（推荐，完全本地）

1. 安装 Ollama：
   - **推荐**：浏览器打开 https://ollama.com/download 下载 Windows 安装包并运行（避免 winget 网络错误）。
   - 若网络正常也可：`winget install Ollama.Ollama`
2. 安装完成后拉取 embedding 模型：
   ```bash
   ollama pull nomic-embed-text
   ```
   或在项目目录下执行：`powershell -File scripts\pull-nomic-embed.ps1`
3. 启动 Ollama 服务（通常安装后自动运行）

无需额外配置，应用会自动使用 `http://localhost:11434`。

可选环境变量：
- `OLLAMA_HOST`：Ollama 地址，默认 `http://localhost:11434`
- `MEM0_EMBED_MODEL`：embedding 模型，默认 `nomic-embed-text`
- `MEM0_USE_OLLAMA=0`：禁用 Ollama，改用 OpenAI

### 2. OpenAI API

若未使用 Ollama，可配置 OpenAI 兼容的 Embedding API：

- `OPENAI_API_KEY`：API Key
- `OPENAI_BASE_URL`（可选）：自定义 baseURL，如 `https://api.moonshot.cn/v1`

注意：需使用支持 embeddings 的模型（如 `text-embedding-3-small`），部分国产大模型 API 可能不提供 embedding 接口。

## 数据迁移

从旧版 SQLite 记忆迁移到 mem0：当前版本会直接使用 mem0，旧数据需手动重新添加或通过脚本迁移。
