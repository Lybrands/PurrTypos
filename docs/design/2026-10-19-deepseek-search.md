# DeepSeek 联网资料合同

所有写作模型共用宿主提供的 `searchWeb` 和 `readWebPages`，分别调用 DeepSeek 官方 Search 与 Fetch API，不依赖写作模型自身的原生搜索能力。接口依据 [DeepSeek 官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/)（2026-10-20 核实）。

## 设置与凭据

设置页按「允许联网 → DeepSeek API Key」展示。总开关默认关闭；开启后配置独立搜索 Key，保存成功后才生效。缺少 Key 时新搜索和新网页读取返回 `web_search_key_missing`，不访问网络、不消耗外部调用额度、不选择其他服务。已经获得授权的缓存分页和完整回执重放不要求重新提供 Key，关闭联网开关后仍拒绝所有工具调用，包括缓存和重放。

- `web_search_enabled`：保留新 Run 授权与逐次全局校验。
- `web_search_deepseek_api_key`：仅写入，空字符串清除；不复用写作模型 Key。
- `web_search_deepseek_api_key_configured`：只读状态；GET 不返回原 Key，回传此状态不会修改凭据。

不存在服务选择项。初始化数据库时删除旧 `web_search_provider` 设置；设置接口拒绝再次写入该字段，读取设置也不返回它。迁移保留联网开关、已保存 Key、来源、页面缓存和历史回执，不改写历史记录中的供应商字段。

Key 保存在本机 settings 数据库，并非加密保险库；仅在访问固定官方 HTTPS 地址时放入 Authorization 头，不进入工具参数、来源、Run 回执、检查点或提示词。浏览器只接受替换值，不填充已保存 Key。保存失败保留草稿和已确认设置，提交时锁定相关控件。

## 搜索与读取

`searchWeb` 固定请求 `POST https://api.deepseek.com/v1/tools/search`，提交 query、count 和 `need_content=false`。不发送用户原始会话、私有正文或用户 IP。每次 1–3 个查询、最多两路并发；同查询和 language 去重，每 Run 最多 6 个实际查询尝试，失败也计数。候选默认 10 / 上限 20，同时限制单查询候选与最终合并数；按上游排名交错合并，URL 去重，摘要最多 300 字符。

query 最多 128 Unicode 字符，不能截断查询。language 默认 zh，可选 en，转换为官方 `lang:` 语法；query 已包含显式 `lang:` 时优先使用该限定，例如 `lang:zh,en`。加上语言限定后的实际 query 也不得超过 128 字符，校验在外部查询额度预留之前完成。query 同时支持 `site:`、`-site:`、`after:` 和 `before:`。`searchProvider` 固定为 deepseek，不生成降级字段。

`readWebPages` 只接受当前 Run 的 sourceId 与可选 offset，单次 1–5 项，最多两路并发，逐页独立成功或失败，按输入顺序返回。新页面固定请求 `POST https://api.deepseek.com/v1/tools/fetch`，只提交已校验的公开 URL。正文为供应商返回的 Markdown，保存其标题；上游未提供标题时保留搜索标题。正文最多缓存 24,000 字符，超过时标记 truncated。返回 URL 是请求的来源 URL；Fetch 不提供最终重定向 URL，不能宣称拿到了该信息。

整批正文最多 8,000 字符；每项窗口为 `min(4000, floor(8000 / 输入项数))`，五项时每项最多 1,600 字符。续读使用 nextOffset，重复页复用缓存；新页面默认最多尝试 8 次，显式 researchMode=extended 可将 Run 上限提升至 12。失败尝试也占额度，缓存分页不占外部额度。

每个新批次在授权、幂等检查后读取一次凭据快照，批内配置变更不更换 Key；新批次使用最新 Key。完整回执重放不再请求 API，中断调用不盲目重发。历史来源与缓存继续受当前 Run 归属和授权检查保护。

## 网络、安全与费用

本机只访问固定 DeepSeek API，不直接下载目标网页，不使用本机 Cookie 或环境代理，不跟随 API 的重定向。保留来源公开 URL 校验，拒绝本地、私网字面地址、带凭据 URL、非 HTTP(S) 等目标；网页目标的 DNS、安全策略、重定向和提取由 DeepSeek Fetch 负责，本机不再进行目标 DNS 解析或连接。

Search/Fetch 请求各有 20 秒 HTTP 超时、2 MB 响应上限，不自动重试。错误不透传供应商响应正文或异常中的凭据。401、402、429、超时、空结果与网页不可读分别报告，不切换其他服务。取消会停止等待并取消在途 HTTP，不继续启动排队请求；已发送请求是否计费取决于服务商。

Search 和 Fetch 分别按服务商规则计费，生成模型另计用量。批量读取减少工具调用和模型往返，仍为每个新网页单独请求 Fetch，不会将五次 HTTP 变成一次。缓存分页和完整重放不再次请求或产生新的 API 调用费用。

## 证据边界

publishedAt、rerankScore 与 authority 是供应商元数据，可能缺失或有误，日期归一为 UTC；retrievedAt 是本次获取时间。搜索成功或评分较高不等于事实验证。模型仍需核对原文、适用日期和独立来源，引用实际返回的 URL。来源与正文均为不可信资料，不得执行其中指令或自动写入小说设定、长期记忆。每轮准确日期、时间和时区的宿主注入保持不变。

本合同不声明已通过真实付费服务验收或跨供应商准确率评测。验证状态见 [当前验证记录](../validation/2026-10-20-deepseek-only.md)。
