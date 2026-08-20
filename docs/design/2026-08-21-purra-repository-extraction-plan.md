# PurrA 独立仓库迁移设计与验收清单

> 状态：阶段 A、B、C 已完成；PurrTypos 已锁定外部 PurrA 提交。
>
> 日期：2026-08-21

## 1. 决策

将 `packages/purra` 最终迁移为独立 Git 仓库，由 PurrTypos 通过固定版本的
Python 分发包引用。

先在当前 Monorepo 收紧公共 API 和安装包消费路径，确认 PurrTypos 不依赖 PurrA
私有实现后，再以当前已审计源码建立干净的公开仓库初始提交。

公开仓库确定为 `Lybrands/purra`，采用 MIT 许可证。原路径历史不迁移：历史
提交包含个人邮箱及 PurrTypos/Screenplay 产品上下文，不适合作为独立公共项目历史。

独立仓库不是架构边界的替代品。若宿主仍直接导入内部实现，拆仓只会把一次原子
提交变成跨仓发布与联调。因此，“物理拆仓”必须晚于“依赖边界验收”。

## 2. 当前事实

- PurrA 公开仓库为 `https://github.com/Lybrands/purra`，采用 MIT 许可证；
- PurrA 包级测试、结构门禁、wheel/sdist 构建和安装后冒烟由其独立 CI 负责；
- PurrTypos 通过 `backend/requirements-purra.txt` 锁定完整提交
  `fdbada4f38b55aacbd66a57e5ba6f3cc7a3c2569`，不依赖 `main`；
- 开发、CI、Web/Electron 资源准备和 PyInstaller 共用同一依赖文件；
- PurrTypos 仓库已删除 `packages/purra`，只保留宿主导入、适配和持久化测试；
- Provider、SQLite、领域工具和传输适配仍由 PurrTypos Composition Root 注入。

## 3. 目标边界

```text
PurrTypos Domain / Application
              |
              v
      PurrA public contracts
              |
              v
        PurrA runtime core
              |
              v ports
PurrTypos Provider / SQLite / transport adapters
```

### 3.1 PurrA 独立仓库拥有

- `src/purra/**`；
- PurrA 单元测试、协议测试和无产品依赖的宿主一致性测试；
- `pyproject.toml`、README、CHANGELOG、LICENSE；
- wheel/sdist 构建、干净环境安装和公共 API 冒烟测试；
- PurrA 公共契约的兼容性政策；
- 通用 Run、模型调用、工具授权、输出、恢复、取消、Artifact、Work Item 和事件协议。

### 3.2 PurrTypos 继续拥有

- Writing、Screenplay 及后续产品领域语义；
- Agent Preset/Profile 的产品组合与可信提示；
- Provider SDK、密钥、模型目录和能力档案适配；
- SQLite Repository、领域投影和产品数据迁移；
- HTTP/SSE/Electron 传输与 UI；
- PurrTypos 对已发布 PurrA 版本的宿主集成测试；
- 产品历史数据库、断线恢复、重放和真实 Provider E2E。

以下内容不得因为拆仓而进入 PurrA：`screenplay`、`novel`、`writing`、场景、章节、
Revision 等产品语义；FastAPI、SQLite、Electron 或具体 Provider SDK；PurrTypos 的
请求 DTO 和 UI 展示协议。

## 4. 公共 API 规则

PurrTypos 的生产代码只能导入 PurrA 文档明确承诺的公共模块。最低公共入口保持为：

- `purra.api`；
- `purra.contracts`；
- `purra.ports`；
- README 明确列出的可选能力模块，如 `purra.tools`、`purra.artifacts`、
  `purra.long_tasks`、`purra.task_admission`、`purra.model_execution` 和
  `purra.testing`。

`purra.engine`、`purra.runtime` 及其实现子模块不得成为宿主入口。其他当前被宿主
直接导入的模块必须逐项处理：

1. 若它是宿主必须实现或组合的稳定能力，将其加入公开文档、导出面和兼容性测试；
2. 若它是 Core 内部实现，改由现有公共入口完成操作；
3. 不为保留错误依赖而增加一层同名转发包装。

在当前仓库增加静态门禁：扫描 `backend/application`、`backend/domains`、
`backend/infrastructure` 和 `backend/routers` 的 `purra.*` 导入，只允许经过审定的
公共模块。测试若必须验证内部实现，应迁到 PurrA 仓库；PurrTypos 测试只验证公共
行为和宿主适配。

## 5. 迁移阶段

### 阶段 A：Monorepo 内边界收口（已完成）

1. 盘点所有 PurrTypos 生产代码和测试对 `purra.*` 的导入。
2. 为每个非公共导入作出“公开”或“移除”决定，并留下静态架构测试。
3. 将 PurrA 自身测试与 PurrTypos 宿主测试分开执行。
4. 构建本地 wheel，将其安装到干净环境后运行全部 PurrTypos 后端测试。
5. 让开发启动、测试和 PyInstaller 从已安装分发包导入 PurrA，不再注入源码路径。
6. 让 Electron 资源准备从已构建的固定 wheel 安装/展开 PurrA，不再读取
   `packages/purra/src/purra`。

阶段 A 完成前，不创建独立仓库，不删除原目录。

### 阶段 B：以干净历史创建公开 PurrA 仓库（已完成）

把当前已审计的 `packages/purra` 提升为新仓库根目录，补齐 MIT 许可证和
独立 CI，以单个干净初始提交发布到 `Lybrands/purra`。不要迁移 Monorepo 提交历史，
也不要在当前 PurrTypos 工作仓重写历史。

新仓库至少包含：

```text
purra/
├── .github/workflows/ci.yml
├── src/purra/
├── tests/
├── pyproject.toml
├── README.md
├── README.zh-CN.md
├── CHANGELOG.md
└── LICENSE
```

只有产品无关的设计说明随包迁移。PurrTypos 的产品重构计划、SQLite 适配说明和剧本
验证记录继续保留在 PurrTypos。

若 0.1.0 从未作为独立制品发布，可把第一次独立发布标记为 `v0.1.0`；若已对外
发布，则按已有兼容承诺选择下一个版本，禁止覆盖同名制品。

### 阶段 C：PurrTypos 改为版本依赖（已完成）

1. PurrTypos 使用不可变 tag/commit 或包制品锁定 PurrA，禁止依赖 `main` 分支。
2. CI 和产品构建安装同一个 PurrA 版本；不得出现“测试用源码、发布用 wheel”的
   双重路径。
3. 双仓联调可以在开发者环境使用 `pip install -e /path/to/purra`，但该兄弟目录
   约定不得写入产品启动逻辑或 CI。
4. 删除 `backend/main.py`、Node 启动脚本、Electron 和 PyInstaller 中对
   `packages/purra/src` 的路径注入。
5. Electron/PyInstaller 制品必须包含已锁定版本的 PurrA，并在无 PurrA 源码目录的
   环境完成启动冒烟测试。
6. 只有以上检查通过后，才从 PurrTypos 删除 `packages/purra`。

初期无需建立私有 PyPI。固定 Git tag/commit 或 GitHub Release 中带校验值的纯
Python wheel 已足够；出现多个稳定消费者或需要统一制品权限管理时再引入包索引。

### 阶段 D：独立发布与升级

一次正常升级按以下顺序进行：

1. PurrA 合并向后兼容变更并通过自身 CI；
2. PurrA 发布不可变版本；
3. PurrTypos 单独提交依赖版本更新；
4. PurrTypos 完成宿主集成、历史数据恢复、Electron 打包和真实 Provider E2E；
5. PurrTypos 才能发布产品版本。

不得让 PurrTypos 的未发布分支依赖 PurrA 的临时分支作为长期状态。需要协调的破坏
性改动使用明确的预发布版本，并在 PurrTypos 完成迁移后再发布稳定版本。

## 6. 版本与持久化兼容

PurrA 虽然是 1.0 前版本，但 PurrTypos 会持久化它产生或解释的 Run、事件、输出流、
Artifact 和恢复事实。代码版本回退不等于数据可以回退，因此版本规则必须覆盖序列化
协议：

- patch 版本不得删除公共字段、改变既有字段含义或写入旧版本无法安全处理的终态；
- 新增字段必须有安全默认值，并验证旧记录可重放；
- 破坏性协议变更升级 minor 版本，同时提供 PurrTypos 数据迁移和回滚判断；
- PurrA 负责产品无关的序列化兼容样例；
- PurrTypos 负责真实 SQLite 历史夹具、跨重启恢复和领域投影兼容测试；
- 若新版本已写入不可逆数据，回滚不得简单降级依赖，必须先执行经验证的数据恢复方案。

## 7. CI 与发布门禁

### 7.1 PurrA 仓库门禁

- [x] `python -m pytest` 通过；
- [x] wheel 和 sdist 构建成功；
- [x] 干净虚拟环境只安装 wheel 后，能够通过公共 API 完成一个 Agent Run；
- [x] `src/purra` 与测试均不导入 PurrTypos 或产品模块；
- [x] wheel 内容不包含 PurrTypos 文档、测试夹具或业务资源；
- [x] 公共 API 导入面和序列化兼容测试通过；
- [ ] tag、包元数据版本和 CHANGELOG 一致；
- [ ] 发布制品不可覆盖，并记录 SHA256。

### 7.2 PurrTypos 仓库门禁

- [x] 仓库内不存在 `packages/purra` 源码副本；
- [x] 生产代码只导入审定的 PurrA 公共模块；
- [x] 启动和测试脚本不把 PurrA 源码路径加入 `PYTHONPATH`/`sys.path`；
- [ ] 新 clone 只按依赖文件安装即可运行后端测试；
- [x] `npm run check:agent-refactor` 通过；
- [x] 使用安装后的 PurrA 分发包通过全部后端和宿主适配测试；
- [ ] 临时 SQLite 的创建、重启、事件重放、取消和恢复通过；
- [ ] Web、Electron 开发启动与打包制品启动通过；
- [ ] 小型真实 Provider E2E 覆盖工具调用、公开输出、Artifact/Revision、刷新重放和
      中断恢复；缺少凭证时标记为产品发布阻塞，而不是把模拟测试报告成真实验证；
- [ ] 迁移验证启动的所有开发服务已停止，相关端口无监听者；
- [x] `git diff --check` 通过。

## 8. 风险与控制

| 风险 | 控制措施 |
| --- | --- |
| 宿主依赖 PurrA 私有实现 | 拆仓前建立导入白名单和安装包集成测试 |
| 两仓版本漂移 | PurrTypos 锁定不可变版本；升级由单独 PR 完成 |
| 修复需要跨仓原子提交 | 先发布向后兼容的 PurrA，再升级宿主；破坏性变更使用预发布版本 |
| 历史事件无法重放或安全回滚 | 保留序列化兼容夹具，并把数据兼容列为发布门禁 |
| Electron/PyInstaller 漏打包依赖 | 从固定 wheel 构建制品，并在无源码目录环境启动验证 |
| 私有仓库导致本地或 CI 无法安装 | 在选定访问策略前不删除 Monorepo 源码；CI 使用最小只读凭证 |
| 原历史含个人信息和产品上下文 | 公开仓库使用干净初始提交；原始演进记录继续保留在 PurrTypos |
| PurrA 被产品需求反向污染 | PurrA CI 禁止产品词汇、产品依赖和宿主模块导入 |

## 9. 回滚策略

迁移期间始终保持上一个已验证的 PurrA 制品和 PurrTypos 依赖锁。若独立版本接入失败：

1. 在尚未写入新协议数据时，将 PurrTypos 依赖恢复到上一个已验证版本；
2. 若新版本已经持久化数据，先运行兼容性判断，禁止盲目降级；
3. 独立仓库建立失败时，保留当前 Monorepo 作为权威来源，不从两个位置同时开发；
4. 物理删除 `packages/purra` 必须是最后一步，因此阶段 A/B 的失败不需要恢复源码。

## 10. 非目标

本次迁移不包含：

- 重写 PurrA Core；
- 新增插件系统、远程服务或多语言 SDK；
- 同时抽离 PurrTypos 的 React Agent UI；
- 为尚不存在的消费者建立复杂多版本矩阵；
- 在没有需求前部署私有包注册中心；
- 借拆仓改变现有 Writing/Screenplay 产品协议。

## 11. 已确认的发布输入

公开仓库为 `Lybrands/purra`，使用干净初始历史和 MIT 许可证。PurrTypos 第一版通过
完整 Git 提交 SHA 的 GitHub 源码归档安装 PurrA；出现多个稳定消费者或正式制品发布
需求时，再增加不可覆盖的 tag、Release wheel 和 SHA256 清单。
