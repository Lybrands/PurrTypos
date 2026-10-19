# 免费联网搜索技术选型与实现

更新日期：2026-10-19。工作分支：`feat/1.0.1`；基线：`main` / `9bb366a7`。约束：中英文资料都重要；搜索和正文提取不依赖收费 API、免费试用额度或搜索 API Key。生成模型原有的 token 费用不在这个“免费”范围内。

## 1. 当前决策

采用 **Bing 公开 RSS 搜索 + feedparser 解析，DDGS 作为免费后备，aiohttp 安全读取网页，Trafilatura 提取正文**，通过 PurrA 原生工具提供给产品 Agent。

| 环节 | 实现 | 选择依据 |
| --- | --- | --- |
| 主要搜索 | 已有 httpx 请求固定 Bing 搜索 RSS 地址，feedparser 解析 | 无搜索 Key；可获取标题、链接、摘要；本次中英文真实请求取得 RSS 结果 |
| 后备搜索 | DDGS 9.16.0，按顺序尝试 DuckDuckGo、Brave 网页搜索 | 无收费 API；封装搜索结果解析；必须显式限制后端，不能用 auto 静默扩大外发范围 |
| 网页请求 | aiohttp，自定义公网 DNS resolver，手动检查重定向 | 保留 HTTPS 验证与原始 Host；把已验证 IP 用于实际连接，避免先检查再重新解析 |
| 正文提取 | Trafilatura 2.3.1 | 本机从已下载 HTML 提取文本，不再付费调用提取服务，不执行页面脚本 |
| Agent 接入 | PurrA 1.0.1 的 ToolRegistration / ToolCatalog | 当前安装的公共 API 足够，无需修改 PurrA 或更换框架 |

Tavily、Exa、收费 Brave Search API 和博查不接入，也不作为自动兜底。本方案中的 Brave 是 DDGS 使用的公开搜索网页，与付费 Brave Search API 是两条路径。

Bing RSS 是公开网页输出方式，不是已获得 SLA 的商业搜索 API。它可能限流、改版或停止提供；开源库也不保证上游服务始终可用。完全免费的准确含义是**不产生搜索/提取供应商 API 账单**，仍有本机资源、网络和维护成本。

## 2. 核实依据与取舍

- DDGS 官方说明支持多个搜索后端，代码使用同步 HTTP 与结果解析。[官方仓库](https://github.com/deedy5/ddgs)、[9.16.0 发布包](https://pypi.org/project/ddgs/9.16.0/)。
- 本次实际检查安装包发现其 Bing HTML 后端 `disabled=True`，不在启用引擎表内。DDGS 对不存在的后端会回退 auto；实现因此先检查配置后端是否启用，再发起请求，避免将禁用 Bing 误标成真正的 Bing 结果。
- 本机直接测试 DDGS 的 DuckDuckGo、Brave、Yahoo、Google、Startpage、Mojeek 路径，没有获得可用结果；一次 Brave HTTP 诊断返回 429。这些结果只描述本次网络和时点，不能推广为这些引擎永久不可用。
- 固定 Bing 搜索地址的 RSS 请求返回 HTTP 200 / XML，并在两个公开中英文查询上各返回 10 个条目；产品适配器只保留最多 5 条。此结果推动主要搜索路径选择 RSS，不把 DDGS 单独调通函数视为完成接入。
- feedparser 从内存中的响应字节解析 RSS，不让解析库自行联网。锁定 6.0.14。[官方文档](https://feedparser.readthedocs.io/en/latest/)。
- Trafilatura 支持将 HTML 转为正文并过滤部分噪声；启发式提取仍可能漏掉正文或纳入页面提示。提取成功不等于事实已验证，也不能保证全文完整。[官方 Python 文档](https://trafilatura.readthedocs.io/en/latest/usage-python.html)。
- aiohttp 支持连接器、resolver、CookieJar 和手动重定向控制，适合将请求边界集中实现。[官方客户端文档](https://docs.aiohttp.org/en/stable/client_advanced.html)。

未选 Playwright/Selenium：当前只需要搜索和静态正文，浏览器运行时会增加安装包、运行资源与跨平台维护成本。未选自托管 SearXNG：它仍依赖上游引擎并增加服务运维；公共实例也不能作为稳定免费保证。[SearXNG API 文档](https://docs.searxng.org/dev/search_api.html)。

## 3. 已实现的产品范围

设置 → 通用 → **允许 Agent 联网查资料**，默认关闭，无需搜索 Key。

打开后，新建的写作 Agent 与剧本普通问答 Run 可获得联网工具。联网授权与原有业务写入审批分别控制；纯问答、小说原文分析、剧本正式生产单元、内部操作与子 Agent 不获得该能力。关闭后，后续工具调用在宿主侧被拒绝；已发出的 HTTP 请求无法撤回。

已有 Run 从持久化 binding 恢复其原始联网授权，设置开关不会给历史任务补授权限。授权由宿主 metadata 和 binding 传递，模型参数及可变 ExecutionState 不作为授权依据。调用时再次检查设置、Run 状态、领域和根 Run 身份。

检索词只由 Agent 显式构造短查询，代码不自动上传章节、资料全文或整段会话。开关说明实际外发路径：先 Bing，失败再 DuckDuckGo、Brave。读取正文由本机请求所选公开网页，不使用用户 cookies 或模型 API 凭据。

## 4. 代码边界

| 文件 | 职责 |
| --- | --- |
| [domains/web_research.py](../../backend/domains/web_research.py) | 免费搜索合同、URL/IP 规则、调用上限及请求准入 |
| [infrastructure/web_research.py](../../backend/infrastructure/web_research.py) | RSS 与 DDGS 搜索、公网连接解析、受限下载、正文提取 |
| [application/web_research.py](../../backend/application/web_research.py) | 授权快照、查询/读取用例、取消、结果分级、分页 |
| [sqlite_web_research_repository.py](../../backend/infrastructure/persistence/sqlite_web_research_repository.py) | 原子调用预留、幂等回放、Run 范围来源和有限正文 |
| [web_research_tools.py](../../backend/agents/shared/web_research_tools.py) | 两个 PurrA 工具、中文操作标签、按请求启用 |
| [agent_composition.py](../../backend/application/agent_composition.py) | 统一装配、宿主授权准备与 binding 留存 |
| [web_research_schema.py](../../backend/database/web_research_schema.py) | 两张表和 Run 删除清理触发器 |
| [WebResearchSettings.tsx](../../src/SettingsPage/WebResearchSettings.tsx) | 显式开关、功能与模型用量提示、加载/保存失败反馈 |
| [external_links.js](../../electron/external_links.js) | Electron 外部 HTTP(S) 来源链接交给系统浏览器 |

依赖锁定见 [requirements-runtime.txt](../../backend/requirements-runtime.txt)。PyInstaller spec 收集 DDGS 动态引擎模块、primp 原生库以及提取组件的数据文件；本次尚未实际构建 Windows 安装包。

## 5. 工具与证据合同

```text
searchWeb(query, language="zh"|"en", maxResults=1..5)
  → success, query, results[{sourceId, title, url, excerpt, backend,
      retrievedAt, publishedAt:null, evidenceKind:"search_excerpt"}], warnings

readWebPages(pages=[{sourceId, offset?}, ...]) // 1–5 项，offset 默认 0
  → success:true, untrusted:true,
    bodyBudget:{pageCount, maxTotalCharacters:8000, maxCharactersPerPage}, results:[
      {success:true, sourceId, title, url, contentRevisionId, content,
        offset, nextOffset, truncated, retrievedAt, evidenceKind:"page_extract", untrusted:true}
      或 {success:false, sourceId, offset, code, error, title?, url?, untrusted:true}
    ]
```

来源编号由宿主生成并绑定当前 Run；模型不能将任意 URL、API 地址或凭据传入读取工具。搜索返回 URL 也经过公开 URL 校验。服务会保留标题、URL、摘要及有限正文，历史工具回执可重放；暂不提供跨 Run 来源复用或独立来源卡片界面。

`language` 只映射地区偏好，不保证结果语言。当前不暴露日期范围或域名过滤参数，因为这些免费路径不能承诺可靠执行此类约束。用户问题要求限定范围时，Agent 需检查实际来源并说明限制。发布时间未知保持 null，不拿检索时间替代。对于可按空格等边界拆分、包含至少两个关键词的查询，若所有结果标题/摘要的最佳覆盖仍少于一半，返回关键词覆盖不足提示；该提示不能代替相关性评测。

工具复用 PurrA 的 READ 模式和公共工具呈现合同。读取批次内部最多两个来源并发，同一来源按输入顺序处理；相同来源和 offset 重复项复用同一结果，不重复下载。首次读取未缓存来源时 offset 必须为 0。结果数组严格按输入顺序返回，单页网络、额度、来源或 offset 错误只影响该项；顶层 `success:true` 代表批次正常处理，不代表所有页面成功。整批输入错误或授权失败返回顶层失败；取消独立传播，并等待活动协程清理，不把取消伪装成页面失败。

已确定多个相关来源时，模型优先批量读取；需先读一页再决定后续来源时，可传单项数组。批量减少工具调用，并能减少原先逐页交互时的模型往返；若模型原先已在同一轮发出多个单页工具调用，模型轮数不一定变化。多个未缓存网页仍各自发起 HTTP 请求，重定向可能增加请求数。单页标签显示“读取 1 个网页（标题）”，多页显示“读取 标题列表 等 N 个网页”。整条记录保持单行，标题超宽以省略号截断，末尾总数和耗时保留；悬停可查看完整标签。全部续读显示“续读”，混合批次在对应标题后标记“·续读”；没有可用标题时使用域名或页面序号，不暴露内部来源 ID。

`bodyBudget.maxCharactersPerPage` 是本批实际每项正文窗口：`min(4000, floor(8000 / pageCount))`。五项时为 1,600，失败、重复或短页不重分配窗口。最终公开答复模型通过 `webPageReadingBudgets` 事实接收本 Run 已完成批次的实际预算；相同预算去重，没有读取回执时不额外添加事实。说明截断原因时使用实际窗口，不能把 4,000 的通用上限当成本次值。

回答策略要求将真实来源链接放在对应结论旁，优先读取关键事实并交叉核对。当前实现**不对答案与来源的语义支持关系做自动验证，也未增加最终答复引用的硬性校验器**；不能声称已经消除幻觉或伪造引用。结构化来源记录与规则为后续校验提供基础。

## 6. 限制、恢复与资源控制

| 项目 | 已实现限制 |
| --- | --- |
| 查询长度与返回规模 | 1–300 字，最多 5 条 |
| 每个获授权根 Run | 最多 6 次新搜索、6 次新页面读取；子 Agent 无联网授权 |
| 搜索外发路径 | 一次搜索先 RSS，再显式 DDGS DuckDuckGo/Brave；一次工具调用最多触达这三个配置入口，非“一次调用只有一个 HTTP 请求” |
| 搜索总等待 | 25 秒；RSS HTTP 时限 8 秒，DDGS 每个后端 6 秒 |
| RSS 下载 | 最大 1 MB，直接解析响应字节；非 RSS、超限、无可用结果进入后备路径 |
| 正文下载 | 仅 HTTP(S) 的 80/443 端口；单次总时限 25 秒，连接 5 秒、HTTP 20 秒；最多 5 次请求的重定向链 |
| 正文大小 | 单页解压后最多 2 MB，保留提取文本前 24,000 字符；整批正文合计最多 8,000 字符，每项最多 `min(4000, floor(8000 / 输入项数))` 字符，5 项时各 1,600 字符；失败或重复项不重分配窗口，续读以实际 `nextOffset` 为准 |
| 读取并发 | 批内最多 2 个不同来源；同来源串行并复用缓存；5 项包括重复来源也不能突破正文总上限 |
| 同步组件 | 全局最多两个工作槽，取消等待不会在后台线程结束前释放槽；不会无界排队 |
| 缓存/回放 | 同一 toolCallId 返回原回执；正文分页读取本 Run 的已存文本，不重复访问外网 |
| 留存 | 来源与有界正文随 Run 保留，删除 Run 时由数据库触发器清理 |

调用开始前，SQLite 事务检查权限和幂等。读取批次保存整个有序输入的摘要和统一结果，不按批次扣读取额度；逐页下载使用确定性的子回执，仍按实际新页面访问尝试计数，失败尝试也占额度。缓存分页不占外网读取额度，不同服务实例并发不能突破每 Run 六次上限。请求中断留下未完成回执，同一调用标识不会自动重新访问外网；已完成页面缓存可被新的调用复用。查询相同但调用标识不同仍计搜索次数，首期没有跨调用查询缓存、刷新或自动 TTL。

DDGS 是同步库，取消等待不能强行终止已在执行的原生 HTTP；该工作继续占用有限槽直到结束，但取消标记阻止它在当前请求结束后继续访问下一后备引擎。RSS 与正文 HTTP 可以随协程取消。实现没有验证码绕过、无限重试、代理池或收费回退。

## 7. 网络与内容边界

- URL 拒绝私网/环回/链路本地地址、IPv4 映射或过渡 IPv6、常见数字 IPv4 变体、非 HTTP(S)、凭据 URL 和非常规端口。
- 网页请求使用自定义 resolver：只接受全部为公网的 DNS 答案，并将相同数值 IP 用于实际连接。每个重定向重新验证 URL 与解析结果，关闭环境代理、cookies 和自动重定向。
- 搜索请求只访问固定引擎入口；不会因 DDGS 某个引擎禁用而让库转为 auto。搜索与正文的代理行为不同：DDGS 自身支持 `DDGS_PROXY` 环境配置，正文读取不继承环境代理。
- 下载不执行 JS、不上传文件、不读取用户浏览器登录态，不读取 PDF 或二进制。登录页、动态站点、反爬页面可能失败；足够长的提示页也可能被误提为正文，使用者应核对来源。
- 工具结果明确标记低信任；宿主加入网页不能作为指令、不能冒充小说原文、不得自动沉淀记忆等规则。提示规则不能单独证明抵御所有网页注入，业务写入仍经过原有工具授权。
- Markdown 来源链接在浏览器新窗口打开，Electron 拦截外部导航并只向系统浏览器传 HTTP(S) URL。没有为新能力暴露通用任意 scheme 的系统打开接口。

## 8. 验证与后续评测

本次验证过程、实际结果与未覆盖项记录在 [免费联网搜索验证](../validation/2026-10-16-free-web-search.md)。不把一次成功外推为大陆或海外网络 SLA。

初始语料仍使用 [40 条公开中英文评测需求](../fixtures/web-search-evaluation-v1.json)，中文 20 / 英文 20；状态为 not_run，尚未跑完整质量评测。语料中的域名与日期约束用于评估当前实现的能力缺口，不表示工具已经支持这些过滤参数。

后续评测区分检索相关性、页面可读率、引用支持率和完整答案成本。固定模型与上下文预算，保留失败和无匹配案例；中英文分别统计，避免总体均值掩盖一侧的不足。无匹配与网络失败分别记录，供应商相关性排序不能当作事实可信度。
