# Writing 冻结实现退役边界

日期：2026-09-14

## 结论

Writing 当前不能按冻结清单整包删除。冻结清单的 118 个路径混合了三种不同性质的
代码：旧 `writing` Profile 及其工具栈、replacement 仍在使用的 Agent 请求合同，以及
与 Agent runtime 无关但仍由产品路由使用的写作技法、故事记忆和历史提案服务。

生产新建入口已经由进程级 rollout policy 固定选择 `writing.purra-native.v1`，但组合根
仍始终安装旧 `writing` Profile，namespace 默认值仍是 `writing`。因此当前不存在生产
新建 fallback，却仍保留了可被测试或部分宿主配置重新选中的旧执行面。下一步应先把
replacement 的请求/响应合同迁到 `agents.writing`，再断开旧 Profile，最后删除闭合的
旧工具与技能目录；不得为历史读取保留旧执行 runtime。

## 真实持久库观察

2026-09-14 对默认数据库
`~/Library/Application Support/purrtypos/purrtypos.db` 使用 SQLite
`mode=ro&immutable=1` 做了两次只读身份/状态盘点。查询没有运行 migration 或写事务，
也没有读取消息、prompt、作品正文、工具参数或结果正文。

第二次观察时间为 20:23 CST，结果与第一次总量一致：

| 实现 | 状态 | 数量 |
| --- | --- | ---: |
| legacy Writing | done | 13 |
| legacy Writing | failed | 4 |
| legacy Writing | canceled | 1 |
| legacy Writing | pending / running / paused / blocked / claimed | 0 |
| replacement Writing | done | 2 |

18 个 legacy Run 均为终态。两次点查证明当前没有需要旧 Profile 接管的活动工作，但不
授权删除历史 Run 或通用 journal。Writing 没有公开的 legacy resume 执行入口；Run
snapshot、SSE 和历史提案读取由通用 Run query 与
`SqliteWritingProposalReadModel` 完成，不需要重新执行旧 Profile。

## 错误前提修正

1. **“冻结文件等于旧文件”不成立。** `writing_agent_service.py` 仍是正式 HTTP 到
   `AgentRunService` 的入口；`writing_chat_request_lifecycle.py` 仍负责请求收据绑定；
   `writing_proposal_read_model.py` 仍负责历史产品事件读取。
2. **“整个 `domains/writing` 都属于旧 Agent”不成立。** 故事记忆、统一记忆、写作
   技法及其 repository contract 仍被章节、记忆、技法、续写和备份等产品能力使用。
3. **“生产仍会新建 legacy Writing Run”不成立。** 普通 lifespan 的 rollout policy
   已包含三个 replacement；真实库中也已有 2 个 replacement Writing Run。问题是组合
   根仍安装旧 Profile、通用 factory 的缺省 policy 仍允许测试/非标准调用者选旧实现，
   而不是普通生产入口当前正在回落。
4. **“保留历史就必须保留 runtime”不成立。** implementation registry 中的 legacy
   identity tombstone 足以为只读 Run snapshot 标注历史版本；Profile registry 不需要
   安装可执行的 `writing` Profile。

## 调用图结果

冻结清单共有 76 个 Python 模块和 42 个 `backend/skills` 文件。以所有非测试、非冻结
Python 模块为生产根进行 AST import 可达性分析，并模拟移除
`composition_factory -> writing_agent_profile` 后：

- 41 个冻结 Python 模块不再被生产根触达；
- 35 个冻结 Python 模块仍被现役生产代码触达；
- 42 个 `backend/skills` 文件只由旧 `WritingSkillCatalog` 动态读取，replacement 工具
  目录不读取这些文件。

AST 不会自动识别动态 import 与 Python 包初始化副作用，因此删除边界又用全文调用扫描
复核，并对两个包壳单独处理。

### 断开 Profile 后的纯旧执行岛

以下职责可以作为同一旧 runtime 删除批次：

- `application/writing_agent_profile.py`、`writing_context_source.py`；
- `domains/writing/adapter.py`、`execution_state.py`、`planning.py`、`policies.py`、
  `evaluation/**`；
- `domains/writing/tools/**`；
- `infrastructure/writing/skill_catalog.py`、`retrieval.py`、
  `knowledge_retrieval.py`、`technique_tools.py`、`tools/**`；
- `backend/skills/**` 中 42 个旧工具 schema/prompt 文件；
- 只验证上述 Profile、context provider、旧 tool catalog/handler/skill loader 的专项测试。

`domains/writing/__init__.py` 是仍有现役子模块的包壳，应从冻结清单移出并保留为中性空
包；`infrastructure/writing/__init__.py` 当前会导出旧 catalog，删除批次必须移除这些
导出并保留中性包壳，不能留下指向已删除模块的 import。

### 仍是现役产品职责，必须从冻结概念中移出

以下文件不属于 legacy runtime，不能为了把冻结清单清零而复制或删除：

- `writing_agent_service.py`、`writing_chat_request_lifecycle.py`、
  `writing_proposal_read_model.py`；
- `writing_memory_context.py`；
- `writing_technique_access.py`、`writing_technique_backup.py`、
  `writing_technique_deletion.py`、`writing_technique_exchange.py`、
  `writing_technique_lifecycle.py`、`writing_technique_runs.py`、
  `writing_technique_service.py`；
- `domains/writing/story_memory*`、`story_settings.py`、`unified_memory.py`、
  `techniques.py` 及仍由这些产品服务使用的 repository/value object；
- `infrastructure/writing/knowledge_vector_index.py`，它仍由
  `novel_knowledge_service` 使用。

这些文件应通过更新冻结清单明确重新分类为现役产品代码。仅为了目录整齐而搬迁它们会
制造大规模无行为收益改动，不属于本次 Agent runtime 退役。

### 必须先由 replacement 接管的 Agent 合同

`application/request_mapping.py` 仍直接使用旧 `domains.writing.context`、
`contracts`、`response` 和 `public_facts`。它们又把 continuity、paragraph/summary
validation、memory context 等一串旧 Profile 合同带入生产闭包。虽然 replacement
Profile 已拥有独立的 scope、context snapshot 和工具目录，但请求构造与最终响应事务
还没有成为 `agents.writing` 自己的闭合边界。

下一批只迁移这条真实依赖链：在 `agents.writing` 定义现行 request context、context
claims、response constraints/validators 和 committed facts provider，让
`request_mapping.py` 不再导入旧 Agent domain。迁移必须保持现有 HTTP wire、Run
binding、工具启用条件和已验证响应事务语义；不保留旧新双合同或 import shim。

## 分批执行顺序

1. **Writing Replacement Product Contract Ownership**：把 request mapping 与响应事务
   所需合同迁入 `agents.writing`；架构测试禁止新路径导入旧 adapter/context/tool 栈。
2. **Writing Composition Runtime Detach**：生产组合根不再 import/安装
   `build_writing_agent_profile`；Writing namespace 默认固定为
   `writing.purra-native.v1`，versioned composition 即使收到遗漏 Writing 的部分 rollout
   policy 也不得重新开放 legacy create。`main.py` 同时停止为 Agent composition 解析
   旧 `SKILLS_DIR`。
3. **Writing Pure Legacy Runtime/Tool/Skill Deletion**：删除闭合旧执行岛、42 个旧技能
   文件及 legacy-only 测试；清理两个包壳的旧导出。
4. **冻结清单收敛**：将确认仍属现役产品的 35 个 Python 模块移出冻结清单；若此时已
   无冻结旧源码，则删除冻结机制本身。不得删除真实历史数据。
5. **验收**：replacement 读写工具、上下文选择、技法使用、请求取消、历史 Run/提案
   回放、全量 backend、frontend、Electron、最终包内容和端口清理全部通过。

## 删除门禁

- 普通 composition 和显式部分 rollout policy 都不能创建 legacy Writing Run；
- production import scan 不再从 router、lifespan、composition 或 `agents.writing` 进入
  旧 Profile/context/tool/skill 栈；
- replacement 的故事背景、人物统计、章节读写、人物/设定/大纲写入和技法上下文测试
  继续通过；
- 18 个 legacy Run 仍可通过通用 snapshot/SSE 只读回放，变更或重新执行不走旧 Profile；
- `backend/skills` 不出现在源码、构建资源或最终应用中；
- 不保留 import shim、旧新双写或“新实现不可用时回落旧实现”。

## 下一开发项

**Writing Replacement Product Contract Ownership 已完成**：新增 replacement-owned
`request_contract`、`context_claims`、`response_contract` 和 `public_facts`。正式
`request_mapping.py` 不再导入旧 `domains.writing.context/contracts/response/public_facts`，
新请求也不再携带只供旧 Profile hydration 使用的空 `writing_chapters`、
`writing_technique_snapshot` 等槽位。HTTP 字段、domain payload locator、Run binding、
响应约束、validator 和公开 facts 语义保持不变。

replacement Profile 同时恢复了原子连续性请求的独立 Judge policy；切流前该 policy 只
由旧 Profile 提供，新 Profile 返回空集合，属于已有能力接线遗漏。完整 Writing 响应
校验套件现直接验证新 orchestrator，并新增架构守卫禁止 `request_mapping` 回到旧四个
domain 模块。

迁移后重新计算调用图：模拟断开旧 Profile 后，冻结 Python 模块中的不可达集合从 41
增至 48，可达集合从 35 降至 28。剩余集合包括 24 个现役产品模块，以及新合同复用的
`continuity_judging`、`continuity_validation`、`paragraph_validation`、
`summary_validation` 四个纯校验原语；它们没有数据库、工具 catalog 或旧 Profile 执行
能力，应在冻结清单收敛时重新分类，不为删除而复制算法。

下一项进入 **Writing Composition Runtime Detach**。本批没有删除冻结源码、迁移数据库
或读取真实创作内容。

## Writing Composition Runtime Detach 执行结果

本阶段已完成。生产 `composition_factory.py` 不再 import 或安装冻结的
`build_writing_agent_profile`，Writing namespace 的唯一 runtime Profile 固定为
`writing.purra-native.v1`。组合工厂同时将三个 Agent kind 都并入 replacement rollout，
因此调用者传入遗漏 Writing、Novel Analysis 或 Screenplay 的部分 policy，也不能重新开放
任何 legacy create 路径；legacy implementation profile 仅保留为历史 Run identity
tombstone，不具备可执行 Profile。

旧技能目录的生产接线已经移除：`main.py`、`config.py`、Electron backend launcher 和 Web
backend launcher 不再解析或传递 `SKILLS_DIR` / `PURRTYPOS_SKILLS_DIR`。架构测试固定了
production composition 不得重新 import 冻结 Writing Profile，且上述生产入口不得恢复旧
技能目录配置。

尚需保留到下一删除批次的旧 Planner、旧检索和旧工具专项测试，只通过
`tests/support/legacy_writing_composition.py` 显式构造冻结 Profile；该 helper 不被生产代码
引用，并将在旧 runtime/tool/skill 测试删除时一并删除。历史 snapshot 查询继续使用
implementation registry tombstone；持久 legacy Writing Run 的执行仍失败关闭。

验收结果：完整 `backend/tests`、Electron backend process 14 项、`compileall`、
`git diff --check` 和 118 文件冻结校验均通过；5174、18321、18322 无监听。本阶段未删除
冻结源码、未访问或修改真实数据库内容，也未执行发布、push 或 tag。

下一项进入 **Writing Pure Legacy Runtime/Tool/Skill Deletion**。

## Writing Pure Legacy Runtime/Tool/Skill Deletion 执行结果

本阶段已完成。原 118 个 Writing freeze 路径由 76 个 Python 文件和 42 个旧技能文件
组成；本批删除其中 45 个纯旧 Python 源文件与全部 42 个 `backend/skills` 文件。删除范围
包括旧 `writing_agent_profile`、eager context source、domain adapter/planner/policy、旧
response/public-facts 合同、旧 tool catalog/schema/handler/cache/retriever，以及只验证这些
路径的测试和已无法运行的旧技法验收脚本。

`domains.writing` 与 `infrastructure.writing` 保留为中性包壳。其余 31 个原冻结 Python
文件经生产 import 和完整回归确认仍承担现役职责：Writing HTTP façade/request receipt/
历史提案，写作技法生命周期，故事记忆与统一记忆，知识向量索引，以及 continuity、
paragraph、summary 等 replacement 正在使用的校验原语。这些文件已退出 legacy freeze，
没有为目录清零复制实现或删除现役能力。

旧 Writing evaluation 只服务已删除的旧工具序列与 planner incident，且三个诊断 HTTP
端点没有产品调用者；相应 evaluation 模块、组合壳和端点一并删除，避免继续把旧工具
合同表述为当前质量门。README 已改为三个 replacement 代码化 catalog 的唯一工具来源。

freeze manifest 当前为空并继续作为临时删除守卫，输出为 `0 files`；冻结机制本身留到
最终验收确认源码、包内容和历史只读路径后再删除。完整 `backend/tests`、测试收集、
Electron backend process 14 项、compileall、diff check 和删除路径扫描均通过；测试端口
无监听。本阶段未访问或修改真实数据库和创作内容，也未发布、push 或 tag。

下一项进入 **Writing Legacy Retirement Final Verification**。

## Writing Legacy Retirement Final Verification 执行结果

本阶段已完成。最终回归确认三个 Agent 的生产新建路径均只安装 PurrA-native Profile，
历史 legacy identity 只用于 versioned query/tombstone，重新执行未安装旧实现仍失败关闭。
Writing 历史 Run、会话与 proposal 的只读重放由后端 query/route fixture 和前端 hydration/
proposal projection fixture 共同覆盖，没有恢复旧 Profile、旧工具执行或双写。

验收时发现 Writing Electron 启动器虽传入专属隔离标志与 marker，Python 进程策略此前只
验证 Novel Analysis 与 Screenplay。现已把 Writing 纳入同一 fail-closed 目录校验，并增加
marked/unmarked 回归；这只修正验收隔离边界，不改变生产实现选择。

最终门禁包括：完整 backend suite、TypeScript、505 项前端单测、38 项 Electron 单测、
历史 Run/提案定向回放、Vite build 与完整 Electron build。默认并发前端单测在所有断言
输出后没有自行退出；相同清单以 `--test-concurrency=1` 完整退出且 505/505 通过，因此本轮
没有用强制退出掩盖结果，也没有借机扩展产品代码。

删除后的源码重新生成 `build-resources`、macOS arm64 `.app` 与 DMG；208 个已删除 backend
路径在构建资源和应用 Resources 中均为 0 命中，`backend/skills`、旧 Writing Profile、旧
Writing tool 目录和空 freeze manifest 均不在包内。PurrA 四个本地候选 wheel 的 SHA-256
与 manifest 一致，`.app` 深度签名和 DMG 校验通过；包未公证、未发布。

Electron 验收只使用两套新建临时目录。开发入口显示 3 个合成人物与合成故事背景；最终
packaged app 从 `app://./dist/index.html` 启动，并显示另一套合成人物与背景。没有读取或
修改真实作品、真实数据库或 Provider 凭据。最后已删除空 freeze manifest、校验脚本和
专项测试；三个 Agent legacy executable source 的冻结生命周期至此结束。

后续 **Three-Agent Retirement Documentation Closure** 与
**Post-Retirement Scope Review** 已完成；跨 Agent 的最终范围结论见
[退役后范围复核](2026-09-14-three-agent-retirement-scope-review.md)。
