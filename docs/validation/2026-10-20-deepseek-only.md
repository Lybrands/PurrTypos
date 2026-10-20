# DeepSeek 唯一路径验证

日期：2026-10-20（Asia/Shanghai）。实现合同见 [DeepSeek 联网资料](../design/2026-10-19-deepseek-search.md)。

## 已验证

- 联网专项后端 156 项通过；全量后端 2,063 项通过，包含每轮宿主日期、时间与时区注入的现有回归。
- 设置组件行为测试 3 项通过：仅展示联网开关和独立 Key；无 Key 显示不可用；Key 不回填；保存、替换、删除、保存失败与重复提交处理正常。
- TypeScript、组件边界检查、git diff --check 通过。
- Web 完整构建、Python 运行依赖打包、本地 Web 产物生成通过。首次构建在受限网络下无法解析依赖源，使用允许联网的构建重试后成功；这不属于业务接口验收。
- 使用 Python -S 只加载打包目录依赖，成功导入 Search/Fetch 适配器、宿主服务与两个工具注册。产物不存在旧 infrastructure.web_research 模块，也不包含 DDGS、feedparser、Trafilatura、aiohttp。

## 工具与 HTTP 合同

HTTP 使用 httpx.MockTransport 模拟 DeepSeek 服务器，未调用真实收费接口：

- 搜索只请求固定 /v1/tools/search，Fetch 只请求固定 /v1/tools/fetch；Bearer Key 不进入回执或检查点；不使用环境代理或跟随 API 重定向。
- 新批次读取凭据快照；批内改 Key 不改变在途批次。旧 free 设置不能路由到其他服务。数据库迁移删除服务选择项，保留 Key、来源、页面缓存与历史回执；设置接口拒绝退休字段。
- 缺少 Key 时不访问网络或消耗外部额度；401、402、429、超时、非法响应、超大响应与空正文分别报错，不回退或自动重试。
- query 及实际附加 lang: 后的长度均受 128 字符限制；显式 lang:zh,en 优先于 language。来源元数据与公开 URL 校验保留。
- 一页与五页读取、最多两路并发、固定输入顺序、部分失败、正文标题、24K 缓存截断、重复页、缓存续读、每 Run 额度、取消和幂等重放通过。五页每项最多 1,600 字符、合计最多 8,000，失败项不扩大其余页窗口。
- 设置 HTTP 路由 → 生产工具目录 → CoreToolExecutor → Search/Fetch 模拟响应 → SQLite 来源与回执通过。双语两个查询加五页读取产生 **2 次 Search + 5 次 Fetch**；工具调用仅搜索和批量读取两次。重放不新增 HTTP 请求。
- 一页、五页的实际工具事件经持久化输出、SSE 映射、前端状态归并和时间线渲染，首次读取与续读的数量、具体标题、单条操作描述均正确。该测试运行前端时间线代码，不等于浏览器视觉验收。

## 未通过与未验证

- 前端全量测试为 611 通过、1 失败。未改动的 src/SettingsPage/useMemoryAutosave.behavior.test.mjs 在 Node v24.17.0 原生清理阶段崩溃，输出 Check failed: node->IsInUse()；单独运行仍失败。未为本次搜索调整修改该无关代码，不能宣称全量检查全部通过。
- **真实付费服务验收：RELEASE BLOCKER / 未验证。** 本地未保存独立 DeepSeek 搜索 Key，因此没有真实 Search/Fetch 请求或真实模型完整搜索对话，也没有验证供应商账户鉴权、余额、延迟或结果质量。
- 没有进行浏览器手动视觉、Electron 应用或 Windows 安装包验收。没有开展跨供应商准确率对比。
- 本次未启动持久化测试服务器，测试用 Vite 实例已关闭；24678 端口无监听。没有重启用户运行中的开发服务。
