# 三类 Agent 的 PurrA 原生重建计划

> 日期：2026-09-12
> 状态：legacy retirement 已完成；三个产品的新建执行均只有 PurrA-native 路径，历史 legacy identity 仅保留只读查询。小说分析目标架构于 2026-09-15 因超长来源容量问题重新进入实施阶段。最终退役范围见[退役后范围复核](../migrations/2026-09-14-three-agent-retirement-scope-review.md)。
> 适用范围：小说创作 Agent、小说分析 Agent、剧本 Agent。
> 执行规则：本文是本次重构的唯一任务台账。开始一个阶段前先确认其前置条件；实现、验证、范围变化和失败证据都追加到本文，不以“代码已经写完”代替验收。

> 2026-09-15 小说分析范围重开：真实 Provider 验收证明现行固定 5 Unit recipe
> 会把全部 segment 重新合并进单次请求，无法扩展到数百万字来源；原 A1-A3 的“完成”
> 只代表旧 replacement 合同曾通过确定性验收，不再代表目标架构完成。新的容量分片、
> 真正 Planner DAG、Map/Reduce/Synthesize 和覆盖门禁以
> [小说分析 Agent 可扩展编排设计](2026-09-15-novel-analysis-scalable-orchestration.md)
> 为准；在该方案切流前不得删除现行 replacement 作为回滚路径。

## 1. 决策摘要

本次不继续在现有三个 Agent 上叠加修补，而采用并行替换的绞杀式迁移：

1. 旧实现保持原路径并冻结，不搬迁、不重排 import、不继续增加功能。
2. 新实现统一写入 `backend/agents/`，以当前 PurrA 的 Root Run + Durable Task + Operation 模型重新接入。
3. 组合根、路由、数据库 schema 和共享持久化保留为迁移接缝，不纳入旧实现冻结清单。
4. 一个 Run 从创建开始永久绑定一种实现；恢复、重试、取消和回放不得跨实现。
5. 新旧实现禁止双写。影子验证只能执行无副作用的读取和计算。
6. 三个 Agent 分别切流、分别回滚、分别验收；不采用一次性总切换。
7. 最后一个旧 Run 退出可执行生命周期且历史读取能力完成迁移后，才删除旧执行代码。

这次重建不会把旧实现的现象全部定义为正确合同。已知错误只作为风险和回归样本；新合同必须根据产品语义、持久化事实和当前 PurrA 能力重新确定。

## 2. 当前事实基线

### 2.1 已共享但没有真正统一的部分

三个 Agent 已由同一个组合根安装 profile，并共享 Agent Run、LongTask、取消控制、孤儿恢复和审批呈现基础设施。但是它们仍分别保留旧的执行、投影、工具授权、Artifact 和进程内状态逻辑，部分代码继续假设一个任务单元会创建独立 Agent Run。

当前 PurrA 的正式执行身份是：

```text
Root Agent Run
  └── Durable Task
      └── Operation(taskId, unitId, attempt)
```

Operation 在所属 Root Run 内执行；框架不会为每个 Unit 创建 synthetic Agent Run。新实现必须以 Operation 作为单元级 provenance、usage、Artifact 和恢复身份，不能依赖给 Root Run 反复写不同的 Part binding。

### 2.2 已确认的代表性风险

以下条目说明为什么需要重建，但不是要求新实现复制旧结构：

- 创作 Agent 的部分工具 schema 已存在，实际工具选择、宿主上下文和展示接线不一致；列表结果还存在截断序列化 JSON 的问题。
- 分析 Agent 的 normalize 输入投影删除了模型需要的观察正文，同时没有授予对应读取工具；当前 recipe 不再生成故事概览，但旧执行分支仍存在。
- 剧本 Agent 仍构造不会落入独立 Run 的 Part `RunBinding`，旧 Run projector 与当前 operation finalizer 并存；候选 Artifact 的恢复身份没有 attempt 维度。
- 三类实现的错误分类、后台任务登记、规范 Turn 查询和生命周期失败呈现存在重复或不一致。

此前通过的确定性测试只能证明被覆盖的现有合同没有破坏，不能证明真实 Provider 会正确选中工具，也不能证明 Electron 中的取消、恢复和历史回放完整。

## 3. 冻结策略（历史 F0 基线，已退役）

本节记录迁移启动时的保护措施，不是现行运行机制。三个 replacement 完成验收后，
`backend/agents/legacy_freeze_manifest.json`、`scripts/verify_legacy_agent_freeze.py`
及其专项测试已于 2026-09-14 删除；最终状态以本文迁移账本和
`backend/agents/README.md` 为准。

### 3.1 已冻结内容

阶段 F0 以 2026-09-12 当前工作树内容为基线，对 194 个旧 Agent production 文件记录 SHA-256。冻结范围包括：

- `backend/application/` 下以 `writing`、`novel_analysis`、`screenplay` 开头的旧实现模块；
- `backend/domains/writing/`；
- `backend/domains/screenplay_agent/`；
- `backend/infrastructure/writing/`；
- `backend/infrastructure/screenplay/`；
- `backend/skills/` 中现有写作技能定义。

冻结检查由以下文件承担：

- `backend/agents/legacy_freeze_manifest.json`
- `scripts/verify_legacy_agent_freeze.py`
- `backend/tests/test_legacy_agent_freeze.py`

修改、删除冻结文件，或继续向冻结目录增加 production 文件，都会使冻结测试失败。

### 3.2 有意保留可修改的迁移接缝

以下内容没有冻结：

- Agent 组合根和 profile 注册表；
- HTTP/SSE 路由；
- 数据库 schema 与共享 repository；
- 通用 Run、事件、取消和审批基础设施；
- 前端共享 Agent 会话组件；
- 测试、迁移脚本和验收脚本。

这些文件只能用于版本路由、兼容读取、共享能力建设或新实现接入，不能成为继续扩展旧实现的后门。

### 3.3 冻结例外

旧代码默认不再修改。只有以下情况可以申请例外：

- 会破坏用户数据或错误执行已经批准的写操作；
- 存在安全、授权或隐私问题；
- 旧 Run 无法取消、导出或安全终止，阻塞迁移；
- 新实现尚未接管时出现生产级完全不可用。

例外必须单独提交最小修复、补回归测试、说明不能在新实现解决的原因，并显式刷新冻结 manifest。普通功能缺失、代码整洁和体验优化不构成例外。

## 4. 目标代码结构

新实现使用以下目录边界；目录可以逐步增加文件，但依赖方向不得反转：

```text
backend/agents/
  shared/
    contracts/          # 实现版本、Operation identity、错误和输出合同
    runtime/            # PurrA profile、Run、Task、Operation 接入
    lifecycle/          # 启动、取消、恢复、孤儿处理、公开呈现
    persistence/        # 新实现共享的兼容 repository/adapters
    streaming/          # 规范事件 cursor/envelope，不含产品专属过滤
    tools/              # 工具能力合同和启动时一致性校验
    testing/            # 三个 Agent 共用的 conformance fixtures
  writing/
    contracts/
    profile/
    context/
    tools/
    application/
  novel_analysis/
    contracts/
    profile/
    recipe/
    tools/
    artifacts/
    application/
  screenplay/
    contracts/
    profile/
    recipe/
    tools/
    artifacts/
    application/
```

依赖规则：

- 产品 Agent 可以依赖 `agents.shared`；`agents.shared` 不得依赖任何产品 Agent。
- 新实现不得 import 旧 profile、service、executor、prompt、tool catalog 或 projector。
- 兼容旧数据只能通过命名明确的 read adapter，不能复用旧执行器。
- 产品领域规则不得为了共用而下沉到 shared。
- PurrA 框架能力与 PurrTypos 产品投影必须分层，不在 adapter 中重新实现框架状态机。

## 5. Run 与数据版本合同

### 5.1 新 Run 的不可变身份

新建 Run 必须持久化以下字段或等价的规范 metadata：

| 字段 | 示例 | 规则 |
| --- | --- | --- |
| `agentKind` | `writing` | 产品 Agent 身份 |
| `implementationId` | `purra-native` | 创建后不可修改 |
| `implementationVersion` | `1` | 决定恢复使用的代码合同 |
| `toolContractVersion` | `1` | 决定可用工具/schema |
| `recipeVersion` | `1` 或空 | 长任务 recipe 的持久化版本 |
| `artifactSchemaVersion` | `1` | 决定 Artifact reader/validator |

旧 Run 在兼容读取时投影为明确的 legacy implementation，不能因为字段缺失而默认走新实现。

### 5.2 路由规则

- 创建：由每个 Agent 独立的 rollout policy 决定 legacy 或新实现。
- 恢复：只读取 Run 持久化的 implementation identity。
- 取消：走共享 control plane，再调用相同 implementation 的产品投影。
- 回放：允许新 reader 读取 legacy 数据，但必须标出来源版本。
- 重试：只在原 implementation 和原 recipe 合同内增加 attempt。
- 降级：新 Run 失败后不能自动改由 legacy 重跑；这会制造双重副作用。降级必须创建新的显式 Run，并关联原失败 Run。

### 5.3 数据迁移原则

- 重构期间数据库变更只允许 additive migration。
- 新旧写路径不能写同一个 owner key 或同一个 Artifact 生命周期。
- 新 Artifact 的 owner identity 至少包含 Root Run、task、unit 和 attempt，或提供等价的 durable operation ID。
- OPEN Artifact 不得简单删除后重跑；必须区分“尚未写入”“写入成功但结算未知”“已经 finalize”。
- 历史 legacy Artifact 保持可读，不在切流阶段原地改写。

## 6. Shared 内核交付物

### S1：框架版本和能力基线

- 锁定实际安装的 PurrA artifact、SHA-256 和源树对应关系；不能只凭 `1.0.0` 版本字符串声称“最新”。
- 列出本次依赖的 PurrA 公共接口及宿主责任，不 import PurrA 私有模块。
- 建立最小启动验证，确保实际 Provider adapter、tool execution、LongTask 和 cancellation 能组合。

完成条件：干净环境安装与候选清单一致；公共接口测试通过；未执行真实 Provider 时明确标记为未验收。

### S2：Implementation 路由

- 定义 immutable implementation identity。
- 在组合根安装 legacy 与 replacement profile，但默认仍路由 legacy。
- 根据 Run identity 分派 create/resume/cancel/replay。
- 增加错误配置和未知 implementation 的失败关闭行为。

完成条件：三个 Agent 都能单独开启或关闭 replacement 路由；旧 Run 永远不会误入新执行器；没有双写。

### S3：规范 Operation 上下文

- 以 `(rootRunId, taskId, unitId, attempt)` 构造 operation identity。
- usage、source provenance、工具读回执、候选 Artifact、stage output 均引用 operation identity。
- 公共查询可以按 Root 聚合，也能回到具体 operation。

完成条件：并行 Unit 不互相覆盖 binding、usage 或 Artifact；重试可区分 attempt；回放仍按 Root 会话呈现。

### S4：错误与恢复合同

统一下列类别，但允许产品 Agent 增加稳定子码：

- `TRANSIENT_PROVIDER`
- `MODEL_OUTPUT_INVALID`
- `TOOL_INPUT_INVALID`
- `TOOL_EXECUTION_TRANSIENT`
- `AUTHORIZATION_REQUIRED`
- `BUSINESS_INVARIANT`
- `CANCELED`
- `EXECUTION_INTERRUPTED`

分类只描述错误性质；是否重试还必须结合 attempt、预算、effect state 和产品策略。取消不能落入普通失败分类，展示失败也不能反向撤销已提交业务结果。

完成条件：三个 Agent 共用 conformance fixture；相同错误类别具有一致的 Run/Unit 状态语义。

### S5：工具能力合同校验

每个 operation 在调用模型前构造一个可验证合同：

```text
projected input
+ enabled tool schemas
+ prompt-required capabilities
+ allowed side effects
+ expected final output
```

启动时或测试中检查提示词要求的工具确实启用、必需字段没有被输入投影删除、写工具的 effect state 和 Artifact owner 已定义。

完成条件：人为制造“提示词要求读取但工具未授权”时测试必然失败。

### S6：生命周期、事件和流

- 统一 canonical Turn identity 查询、`event_id IS NOT NULL` 过滤和冲突检测。
- 统一后台任务登记，但数据库仍是权威；进程内 registry 只负责本进程 task handle。
- 统一 SSE cursor/envelope；产品可见性过滤仍由各 Agent 拥有。
- 启动失败只由一层完成持久化 settlement；其他层只呈现或附加诊断。

完成条件：断流不取消后台 Run；显式取消可跨重启观察；同一失败不重复结算。

## 7. 三个 Agent 的迁移任务

### W：小说创作 Agent（第一个迁移）

选择它作为第一个纵向样本，因为读取书籍上下文、查询人物和章节、审批写入可以形成较短的真实验收闭环。

#### W1：只读能力

- 明确定义当前书籍、当前章节和会话的宿主绑定。
- 接入故事背景、人物列表、人物总数、章节列表、全局大纲和设定实体查询。
- 所有列表使用结构化分页或先裁剪对象再完整序列化，禁止截断 JSON 字符串。
- “本书有多少人物”必须由工具结果计算，不允许模型凭上下文猜测。

完成条件：真实 Provider 能稳定选择正确工具回答“查看故事背景”“本书共有多少个人物”；答案与数据库一致；工具不可用时返回稳定、可诊断原因。

#### W2：写入和审批

- 章节编辑、人物/设定修改和大纲修改保持 propose/approve/commit 分离。
- 清空章节必须具有显式语义，不能由缺字段降级为空字符串触发。
- 写操作具备稳定幂等 identity、effect state 和取消线性化。

完成条件：批准前不改变业务数据；重复批准不重复写；取消和断流边界有确定性测试。

#### W3：上下文和长期记忆

- 区分单轮输入、累计会话、缓存输入、书籍知识和续写来源。
- 工具读取事实不得被重复注入为隐式模型事实。
- 明确 `allowWithoutTechniques` 等旧参数在新合同中删除还是实现，不继承无效开关。

### A：小说分析 Agent（第二个迁移）

#### A1：版本化 recipe

- 新 recipe 明确 extract、normalize、evidence validate、overview、technique distill、coverage 和 review 的依赖图。
- 明确故事概览是必需、可选还是独立产物；不存在“executor 支持但 recipe 永远不生成”的分支。
- planner 只能映射展示阶段，不能改变冻结来源范围和 host recipe。

#### A2：有界材料访问

- normalize 必须能看到完成归并所需的 observation ID、正文和证据，或获得受限分页工具。
- 输入投影、工具授权和提示词由 S5 自动校验。
- 引用采用短 handle 和宿主重建，模型不得改写来源引文。

#### A3：Artifact 与恢复

- 业务 Artifact 提交成功后，阶段播报失败只能影响展示，不得把业务 Unit 变回未完成。
- 明确 interrupted attempt 已写 Artifact 的恢复：验证并继续 finalize，或创建新 attempt Artifact；不得靠 digest 冲突失败关闭。
- review 只接受当前 schema，并为 legacy review 提供只读兼容路径。

完成条件：分片并行、取消、重启、可恢复 Provider 故障、invalid evidence 和最终 review 均有测试；真实 Provider 新 Run 能形成可引用产物。

### P：剧本 Agent（最后迁移）

#### P1：Part/Operation 合同

- 所有 Part kind 使用一个枚举/注册表生成 recipe、checkpoint 映射、工具权限和展示信息，禁止手写多份字符串映射。
- 删除 replacement 路径中的 Part `RunBinding` 假设；operation identity 是唯一单元身份。
- structure、episode metadata、scene list、draft scene、review 等 Part 都声明输入 revision scope。

#### P2：Candidate Artifact

- tool-written candidate 与 host-captured candidate 使用显式不同的完成合同。
- 必需写工具没有调用时归为有界的模型输出错误，而不是永久业务失败。
- Artifact identity 支持 attempt；写入成功但 finalize 未知时优先对账，不盲目重新调用模型。

#### P3：Revision provenance

- 区分“允许浏览历史 revision”和“允许作为本次候选证据”。
- 正式候选必须记录实际消费的 revision receipts，并满足 operation 的 admissible revision scope。
- 不把同项目权限检查误当成冻结基线验证。

#### P4：Checkpoint、usage 与恢复

- checkpoint 从 operation receipts 投影，不从不存在的 Part Run binding 重建。
- usage 记录到 operation，再聚合到 task/root；结算附属错误不得覆盖模型原始异常。
- CAS 冲突使用有界退避或 repository 原子能力。

完成条件：每种 Part 至少覆盖成功、漏写候选、Provider 暂时失败、写后中断、重试、取消和恢复；全剧本生成通过真实 Provider/Electron 验收。

## 8. 切流策略

切流单位是“一个 Agent 的新 Run”，不是整个系统，也不是单个 Run 中的某些 Unit。

建议顺序：

1. 开发/测试环境显式启用 replacement。
2. 内部 fixture 和隔离项目运行；禁止接触真实创作数据。
3. 真实 Provider 的新测试项目验收。
4. Electron 中验证工具展示、审批、取消、恢复和历史回放。
5. 默认创建 replacement Run，同时保留快速关闭开关。
6. 稳定观察窗口结束后，关闭该 Agent 的 legacy 新建入口。

回滚只影响新 Run 的创建路由。已经创建的 replacement Run 继续由 replacement 负责安全终止或恢复；不能让 legacy 接管。

## 9. 测试和验收矩阵

| 层级 | 证明内容 | 不能替代 |
| --- | --- | --- |
| 单元测试 | schema、分类、纯函数、状态转换 | 数据库事务和真实 Provider |
| repository/事务测试 | 幂等、CAS、Artifact、取消竞争 | 模型工具选择 |
| PurrA conformance | Run/Task/Operation 框架合同 | PurrTypos 产品投影 |
| 路由兼容测试 | 新旧 Run 固定进入正确实现 | Electron 行为 |
| API/SSE 集成 | cursor、重放、公开字段 | 桌面渲染和交互 |
| 真实 Provider | 工具选择、结构化输出、重试行为 | Electron 窗口体验 |
| Electron 验收 | 用户可见全链路 | 发布产物一致性 |
| 打包/产物验证 | wheel/npm/Electron 资源准确 | 线上运行质量 |

每个阶段记录：执行命令、退出码、环境、是否 mock、是否真实 Provider、是否 Electron、是否启动进程，以及进程停止和端口释放证据。

## 10. 旧代码删除门槛

每个 Agent 独立满足以下条件后，才删除其旧执行代码：

- 新 Run 创建已完全切到 replacement，并经过约定稳定窗口；
- 不存在该 Agent 的 running、paused、blocked legacy Run；
- legacy Run 的历史查看、导出和取消已有兼容实现；
- replacement 能读取需要继承的 legacy 成果，且不会改写旧 Artifact；
- 真实 Provider、Web/Electron、重启恢复和打包验收完成；
- 回滚窗口结束且没有未解决的数据一致性事故；
- 删除旧代码后的全量测试和历史 fixture 回放通过。

如果历史 Run 需要长期保留，只保留最小 legacy reader/schema adapter，不保留完整旧执行器、工具目录或 projector。

## 11. 明确非目标

- 本计划不承诺保持旧实现中的错误行为。
- 不在第一阶段统一所有产品 API 或 UI。
- 不把三个 Agent 的领域 recipe 合并成一个通用 recipe DSL。
- 不把 PurrTypos 的数据库、Provider 策略或 UI 投影下沉进 PurrA。
- 不在没有用户授权时迁移或重写真实创作数据。
- 不发布、push、tag 或更新外部 PurrA 包。

## 12. 初始阶段台账快照

下表保留 2026-09-12 建立计划时的阶段状态，不随之后每个小批次重写；不能用于判断
当前完成度。现行结果以第 14 节执行账本和本文顶部状态为准。

状态只能使用：`未开始`、`进行中`、`完成（确定性）`、`完成（真实验收）`、`阻塞`。

| ID | 交付物 | 状态 | 下一门槛 |
| --- | --- | --- | --- |
| F0 | 旧实现冻结清单、校验脚本、新目录骨架 | 完成（确定性） | 计划文档确认 |
| F1 | 旧 API/事件/数据库/工具合同 characterization inventory | 完成（确定性） | 进入 Shared 内核 |
| S1 | PurrA artifact 与公共接口基线 | 完成（确定性） | artifact 变化时重新验证 |
| S2 | immutable implementation 路由 | 完成（确定性） | 三 Agent 分别完成真实切流门禁后复核 |
| S3 | Operation provenance | 未开始 | 并行与重试测试 |
| S4 | 统一错误与恢复合同 | 未开始 | 三 Agent conformance |
| S5 | 工具能力合同校验 | 未开始 | 负向测试 |
| S6 | 生命周期、规范事件与流 | 未开始 | 取消/重放测试 |
| W1 | 小说创作 replacement 只读能力 | 进行中 | 真实 Provider + Electron |
| W2 | 小说创作 replacement 写入与审批 | 已完成 | W3 上下文与长期记忆 |
| W3 | 小说创作 replacement 上下文与长期记忆 | 完成（确定性） | 真实 Provider + Electron 验收 |
| A1 | 小说分析版本化 recipe 与 admission | 完成（确定性） | A2 有界材料访问 |
| A2 | 小说分析有界材料访问 | 完成（确定性） | 随 A3 Unit executor 做模拟 Provider 接线验收 |
| A3 | 小说分析 Artifact 与恢复 | 完成（确定性） | 真实 Provider/Electron |
| P1 | 剧本 Part/Operation 合同 | 完成（确定性） | P2 Unit executor 纵切 |
| P2 | 剧本 attempt Artifact、executor 与恢复 | 完成（确定性） | 真实 Provider/Electron 随 P4 验收 |
| P3 | 剧本 admissible scope 与实际读取 receipts | 完成（确定性） | 真实 Provider/Electron 随 P4 验收 |
| P4 | 剧本 Revision/checkpoint 投影 | 完成（真实验收） | 独立 Screenplay rollout 与回滚开关 |
| C1 | 分 Agent 切流与稳定观察 | 未开始 | 删除评审 |
| D1 | 删除旧执行器，保留必要 legacy reader | 进行中 | Writing 共享岛抽取与退役 |

## 13. 决策记录

| 日期 | 决策 | 原因 |
| --- | --- | --- |
| 2026-09-12 | 冻结当前旧实现，在新目录重建 | 现有实现混合旧假设和新 PurrA Operation 语义，继续局部修补会扩大迁移成本 |
| 2026-09-12 | 旧文件保持原路径 | 先搬目录会制造无业务收益的 import churn，并增加与当前未提交改动冲突的风险 |
| 2026-09-12 | 组合根、路由、schema 和共享 persistence 不冻结 | 它们是版本路由与兼容迁移所必需的接缝 |
| 2026-09-12 | 不允许新旧双写 | Agent 工具具有真实副作用，双写难以对账且会破坏幂等性 |
| 2026-09-12 | Unit 使用 Operation identity，不恢复 synthetic Part Run | 与当前 PurrA 的 owning Root Run 合同一致，并保留 attempt 粒度 |
| 2026-09-12 | 创作、分析、剧本依次迁移 | 先用较短的工具查询/审批链验证 shared 内核，最后处理最复杂的 Candidate/Revision 工作流 |
| 2026-09-12 | “本书人物总数”只统计当前书自有 `characters` 行 | 继承来源和知识库投影是只读参考事实；混入同一 total 会让统计口径随外部绑定变化且无法审计 |
| 2026-09-12 | 双版本 Profile 共用一个 `AgentComposition` | 两个独立 composition 即使共用 SQLite，进程内事件通知、审批、活跃 Core 与 shutdown 所有权仍会分裂；同一 composition 内按 host-owned route metadata 选 Profile 才能保持单写和统一生命周期 |
| 2026-09-12 | W3 只把选择清单写入 ExecutionState/Run binding，正文必须经 READ 工具进入模型 | locator 是授权边界，不是模型事实；避免旧实现把缓存正文、选中资料和工具结果反复注入每个模型回合 |
| 2026-09-12 | 删除 `allowWithoutTechniques`，不在 replacement 合同中实现 | 该参数从未改变创建行为，却进入幂等 digest；保留会制造“开关有效”的错误承诺和无意义的 409 冲突 |
| 2026-09-15 | 小说分析的分片数量、每片章节和字符范围由 Host Slice Compiler 确定 | Planner 不读取正文且不得改变容量与覆盖边界；单片来源最多占所选 context window 的 40% token |
| 2026-09-15 | 小说分析改为真实 Planner DAG + Map/Reduce/Synthesize/Coverage | 固定 5 Unit recipe 无法扩展到数百万字来源；新方案见独立可扩展编排设计 |

## 14. 变更记录

| 日期 | 阶段 | 记录 |
| --- | --- | --- |
| 2026-09-12 | F0 | 以当前工作树为准冻结 194 个旧 Agent production 文件；新增自动校验和 `backend/agents/` 四个 package 骨架。冻结测试与 Agent composition 定向测试通过。未修改生产路由，所有新 Run 仍走现有实现。 |
| 2026-09-12 | 计划 | 建立本计划。正式重构尚未开始；下一步必须从 F1 合同 characterization inventory 开始。 |
| 2026-09-15 | A4 重新规划 | 小说分析目标架构范围重开。确认 Host 在模型与窗口选定后，以 40% token 上限按章节优先、超长章节内部切分的算法生成不可变 SliceManifest；Planner 只规划真实 Map/Reduce/Synthesize DAG。下一实施项回到容量元数据和 Slice Compiler，不继续扩展固定 5 Unit recipe。 |
| 2026-09-15 | A4 容量与 Slice Compiler | 来源 section 已持久化 byte/character metrics，新增按 tokenizer id/version 的 token metric 缓存和旧库 additive 回填。确定性 Slice Compiler 实现 40% token 上限、完整章节优先、超长章节内部切分、稳定 slice identity 与无丢失/无重复覆盖校验。20 项定向测试通过；500 万字合成单 section 在 1M 窗口下生成 13 片且最大片不超 400,000 token。尚未接入旧固定 recipe 或真实数据；下一项是 Planner Contract。 |
| 2026-09-15 | A4 Planner Contract | 新增严格、版本化 AnalysisPlan，Planner 只决定允许维度内的语义 pass、Reduce fan-in、整书综合结构和质量检查；输入仅含 Source/SliceManifest 元数据，不含原文。Host 把该合同展开为 Map/分层 Reduce/Synthesize/Coverage/Review 真实 DAG，绑定 recipe digest，并在执行前拒绝超额 pass 和模型调用。尚未接入生产 Profile；下一项是 Map Child 执行。 |
| 2026-09-15 | A4 Map Child 第一纵切 | 实现 Host-bound `readNovelSourceSlice`，模型无法提供 sliceId 或读取其他范围。Map executor 要求真实 Child Run，校验其 `root_run_id/parent_run_id`，并以 Child 作为 attempt Artifact 创建者；同 attempt 重放不重复调模型。模型输出错误归类为可重试，Run 归属冲突为不可重试。实施核对确认 PurrA 现行 Recipe dispatcher 显式禁止 Unit 使用独立 Run，下一小项必须是基于正式 Run-tree command 的 Tree-aware dispatcher，不允许手工插入 Run 冒充接入。 |
| 2026-09-15 | A4 Map Run-tree 命令适配 | 进一步核对后纠正“必须复制 dispatcher”的假设：Unit executor 可以通过当前 AgentCore 的公开 `spawn_agents`/`join_agent_runs` 拥有真实 Child，Recipe settlement 仍留在原 dispatcher。Composition 新增可选 Core binder；Map runner 使用 task/unit/attempt 幂等 spawn key，Child 只获得单一 slice 读工具且无 spawn 权限，工具从 Run-tree `input_payload` 解析 Host scope。105 项相关回归通过。现行 v1 Profile 的 Child 会再次触发整书 admission，所以未将工具接入 v1；下一项是 v2 Profile 组装与 Root/Map Child 模式隔离。 |
| 2026-09-15 | A4 Scalable v2 Profile 组装 | 新增隔离 `novel_analysis.scalable.v2` Profile/composition，未改生产 registry。Root 在 Planner 前以模型窗口编译 SliceManifest，只向 Planner 提供元数据，并将 AnalysisPlan 编译为 scalable recipe；Root 的 Map 工具 enablement 为空。Child 通过 Agent-tree metadata 识别，强制 Reactive，只能使用 `readNovelSourceSlice`，且 admission 失败关闭。同时删除 v2 对 v1 segments scope 的依赖：请求只保留 revision/command，不再在五百万字场景携带旧 16K segment 目录。109 项相关回归、compileall 和 diff check 通过。下一项是在隔离 composition 中以模拟 Provider 跑通 Root→Child→read tool→attempt Artifact；未切流。 |
| 2026-09-12 | F1 | 主 Agent 与三个并行子 Agent 完成 shared、writing、novel-analysis、screenplay 四份 legacy 合同盘点；分别记录当前事实、禁止继承缺陷、待决策、数据兼容、测试资产和真实验收缺口。确认 Writing 背景/人物能力的主要断点是 knowledge purpose 动态授权，而非工具未注册；人物总数缺少宿主 `total`。冻结文件保持不变。 |
| 2026-09-12 | S1 | 固定 replacement 使用的已安装 PurrA 1.0.0 本地候选及四个 wheel SHA-256，记录允许使用的公共模块和 Root Run + Durable Task + Operation 框架假设。该结论只具有 package/确定性证据，不代表发布、真实 Provider 或 Electron 验收。 |
| 2026-09-12 | S2 | 开始实现纯新代码的 immutable implementation identity：支持 writing、novel-analysis、screenplay，缺少持久身份的历史 Run 只能解析为 dated legacy，不能默认进入 replacement。尚未接入数据库或生产路由。 |
| 2026-09-12 | S2 | 新增 `ai_agent_runs` 六个 nullable implementation identity 字段和一次性写入触发器；legacy Run 保持全空。新增 identity store，并通过 canonical Run-begin projector 在 Run 创建事务内原子写入 replacement identity；无效 identity 会同时回滚 Run 与 started event。组合根已串联通用 identity projector 与原 screenplay begin projector，但现有 profile 均未提供 replacement identity，因此生产创建路由仍完全保持 legacy。replacement identity/store、schema migration、Run repository、PurrA package、LongTask binding、事件流、取消控制等定向测试共 110 通过；另有 composition + screenplay durable service 集成组 180 通过。冻结校验继续为 194 文件一致，`git diff --check` 通过。 |
| 2026-09-12 | S2 | 完成 exact-version implementation registry、三个 Agent 独立 rollout policy、create/resume/cancel/replay route resolver、历史 Run kind 严格推断、Root/Child legacy 继承和未知版本失败关闭。新 legacy Run 在 canonical begin transaction 内写入明确 identity；历史空字段仍标记为 `legacy_inferred`。通用 Run snapshot 现在公开来源版本，取消产品投影按 runtime profile 分派，避免未来 replacement Run 误触 legacy screenplay projector。组合根只注册真实存在的三个 legacy profile；配置未安装 replacement 会在启动时失败，不允许身份与执行器假切流。相关路由、回放、取消、组合和剧本耐久测试组 184 项通过。S2 仍保持进行中，因为三个真实 replacement profile 尚未实现和安装。 |
| 2026-09-12 | W1 | 新增不依赖冻结 Writing 工具实现的 SQLite read model、7 个 READ 工具、`writing.purra-native.v1` profile 和仅用于隔离验收的 composition。人物目录由宿主返回 `total/items/nextOffset`，知识库 purpose 不再移除背景与人物能力；书籍、会话、章节三重 scope 失败关闭；大纲和列表返回完整 JSON，对长正文先裁剪字段再序列化。完成模拟 Provider 的 Core→tool→receipt→后续模型消费链，证明 tool receipt 中的 `total=3` 能进入后续模型调用，并原子持久化 replacement identity。该证据是确定性 wiring，不代表真实 Provider 会自主选中工具。生产组合根及路由仍保持 legacy。 |
| 2026-09-12 | S2/W1 | 新增 version-aware Profile registry 和请求路由接缝，允许 legacy `writing` 与 `writing.purra-native.v1` 在同一 composition、同一 output publisher、同一审批与 Core 生命周期中共存。新 Run 按 rollout policy 选择，operation resume 按持久 implementation identity 回到原 Profile；调用方不能用 metadata 覆盖宿主选择。新增 `create_versioned_agent_composition` 作为显式接入入口，默认生产 `create_agent_composition` 仍只安装 legacy。确定性模拟 Provider 已通过该双版本 composition 完成工具回合并持久化 replacement identity；legacy composition、路由、Run control、剧本 durable 与 SSE 定向回归通过。冻结 194 文件保持一致。尚未完成真实 Provider 和 Electron 验收。 |
| 2026-09-12 | S2/W1 接入修正 | 真实 Run `run_dfb1a8bb940b408d` 证明仅新增显式 versioned factory 并不构成接入：生产 lifespan 仍创建 legacy composition，导致“当前的故事背景是什么”进入冻结 `writing`，最终因无关的章节读取触发 `knowledge_chapter_outside_scope`。现已将应用启动入口切到 versioned composition，并将所有新 Writing Run 默认路由到 `writing.purra-native.v1`；legacy Writing 只为已有 Run 的恢复和回放保留。启动级测试断言 replacement Profile 已安装且 create route identity 为 replacement。运行中的 Electron 后端必须重启后才会加载该改动，真实 replacement Provider 验收仍待重启后新建 Run。 |
| 2026-09-12 | W2 | 在 replacement Writing Profile 新增 `getChapterContent` 与 `editChapterContent`。读取工具返回宿主 SHA-256 `baseRevision`；编辑工具使用 PurrA `CONFIRM`，审批前不执行 handler，批准后在取消线性化事务中执行 revision CAS。`content` 与 `baseRevision` 均为 schema 必填，空内容还必须显式提供 `clearContent=true`；revision 过期在审批前失败。提交返回稳定 `intentDigest` 与 committed revision，精确重放识别为 noop，拒绝和预提交取消均不改变正文。相关 Writing、composition、lifespan、routing、Run control 和 SSE 定向测试 175 项通过；冻结 194 文件一致。人物、设定、大纲写入以及真实 Provider/Electron 审批仍未完成。 |
| 2026-09-12 | W2 | 在同一 replacement Profile 补齐 `editStoryBackground`、`updateCharacter`、`updateSettingEntity`、`editGlobalOutline`，至此 profile 共暴露 12 个新实现工具。四类更新均采用 read 返回宿主 `baseRevision`、PurrA `CONFIRM`、审批前 scope/revision 校验、批准后取消线性化事务 CAS、稳定 `intentDigest` 和精确重放 noop；跨书 ID 失败关闭，背景和总纲的清空需要 `clearContent=true`，人物与设定只做显式字段的部分更新。提交同步写入既有 history 表。由于 `setting_entity_history` 不保存类型，本阶段不开放 `entityType` 修改，避免制造不可完整回滚的历史。Writing、composition、lifespan、routing、Run control 和 SSE 定向回归 196 项通过，12 个工具的组合合同有效，冻结 194 文件一致。W2 的确定性实现已完成；真实 Provider 自主选中工具、Electron 审批 UI 与批准后实际写入仍需重启当前桌面后端并用隔离数据验收。 |
| 2026-09-12 | W2 真实验收 | 使用当前工作区源码、独立临时用户目录和隔离测试书执行真实 GLM-5.3-Flash + Electron 验收。首个 Run `run_bb476d1a3302437f` 正确完成 `getStoryBackground → editStoryBackground → approval`，但批准后失败关闭且旧背景未变；持久事件定位为生产 `SqliteToolIdempotencyGateway` 已持有取消线性化事务，而新 repository 再次开启同类事务导致禁止嵌套的 `RuntimeError`。已将章节和资料 repository 改为无宿主事务时自建线性化事务、有宿主事务时加入并使用 savepoint，并补两项生产形态回归。修复后新 Run `run_9a2e25d4bbbb4845` 由真实 Provider 自主完成读取与编辑工具选择；审批前旧内容不变，批准后 Run 状态 `done`、identity 为 `writing/purra-native/v1`、receipt `effect_state=committed`、history 新增记录，数据库与 Electron 故事背景面板均显示新内容。完整定向回归 198 项通过，冻结 194 文件一致。W2 以该代表性写入纵切完成；人物、设定、总纲和章节的各自 CAS/拒绝/重放由确定性测试覆盖。 |
| 2026-09-12 | W2 打包验收 | 使用正式 `npm run build:electron` 从当前工作区重建 macOS arm64 应用和 DMG。首次打包暴露三个安装态问题：自定义 `app://.` Origin 未被后端 CORS 允许，导致全部 API 预检 400；`.npmrc` 的 electron-builder 镜像缺少 `dmg-builder@1.2.0`；`@lexical/yjs` 的 `yjs` peer 依赖未安装，依赖收集退化。已精确允许 `app://.` 并保持不受信 Web Origin 失败关闭，移除失效的 DMG 镜像覆盖，补入 `yjs`。最终 DMG `PurrTypos-0.5.2-arm64.dmg` 经 `hdiutil verify` 和 ad-hoc `codesign --verify --deep --strict` 通过；包内 `backend/agents` 与工作区逐文件一致，PurrA wheel SHA-256 仍为 `9a3695b9fa2f37bc11b140c0077c5c1273c5b352fc2e13579af99ff859a03c2c`。使用新打包 `.app`、隔离用户目录和真实 GLM-5.3-Flash 执行 Run `run_6f9cf347fea3413f`：Provider 自主完成 `getStoryBackground → editStoryBackground`，批准前内容不变，批准后 Run `done`、identity `writing/purra-native/v1`、receipt `committed`、history 记录为 2，界面与 SQLite 均显示新背景。该 DMG 仅是本地验收产物，未 notarize、未安装到 `/Applications`、未发布。 |
| 2026-09-12 | W2 签名保持验收 | 上述打包应用首次运行后，包内 Python 尝试在已签名 `Resources/backend` 写入 `__pycache__`，导致 `codesign` 报 sealed resource 被改动。已在打包后端进程环境强制 `PYTHONDONTWRITEBYTECODE=1` 并补 Electron 单测。重建后应用启动、`app://.` CORS 预检与设置读取均成功；退出后再次执行 `codesign --verify --deep --strict` 仍通过，后端进程和 18321 端口均已清理。最终 DMG SHA-256 为 `513651a37f41073700f4f3bb44513c3e914f8dc6d47538ada779cd2d90e48fbe`。 |
| 2026-09-12 | W3 第一批 | 新增 replacement 专属 `WritingContextSelection`：关联章节/大纲、灵感、语义长期记忆、伏笔和技法输入的 locator 被规范化后写入 ExecutionState 与 Run binding；模型只看见有界的选择计数和读取策略，不接收 locator 对应正文。新增 `readAssociatedWritingContext` 与 `readSelectedWritingContext`，前者只能读取冻结选择清单的本书章节/大纲，后者显式读取本书灵感/伏笔；越界 ID 失败关闭。语义长期记忆和写作技法尚未接入时返回/呈现明确 pending，不伪装为无选择或空结果。删除全链路无效参数 `allowWithoutTechniques`，旧字段由请求 schema 明确拒绝，不再污染创建幂等 digest。本阶段仍为进行中。 |
| 2026-09-12 | W3 第二批 | `readSelectedWritingContext` 已接入组合根持有的实际 `MemoryComponentResource`，按本书与冻结 ID 读取 active 语义记忆，并返回版本、来源和缺失清单；组件未配置时返回 `memory_component_unavailable`，不回退旧表。新增 `readWritingTechniqueContext`，只读取本轮 input locator 授权且仍有效的 `SKILL.md` 入口，并对合计字符预算失败关闭；新增 `listContinuationSourceSections` / `readContinuationSourceSection`，通过冻结 continuation binding 限制在分叉点之前。Agent composition 现在显式把共享 memory resource 注入 Profile factory。相关 Writing、composition、lifespan、continuation 和冻结定向测试 157 项通过。W3 仍需补齐 Run 恢复时的版本快照复核、技法自动选择路径和真实 Provider/Electron 验收。 |
| 2026-09-12 | W3 确定性完成 | 创建 Run 前生成不含正文的 `writingContextSnapshot`，冻结语义记忆 ID/version、技法 input request/snapshot digest，以及 continuation binding/canon digest；快照同时进入 ExecutionState 和 Run binding，工具恢复时优先读取持久 binding。记忆版本、技法输入或续写绑定发生变化均失败关闭，不能在同一 Run 中静默换上下文。自动技法模式新增候选目录工具，并把首次选中的 candidate refs 固定在 ExecutionState，后续改选失败。公共会话继续使用有界完整 user/assistant turn，工具正文只存在于显式 READ 的当前工具历史。W3 达到确定性完成；真实 Provider/Electron 仍是独立验收门。 |
| 2026-09-12 | A1 开始 | 在 `backend/agents/novel_analysis/recipe.py` 建立 replacement recipe v1：`extract → normalize → validate_evidence → overview / distill_technique → coverage → review`。`storyOverview` 被定义为独立且必需的 Artifact，而不是不存在的可选字段；技法阶段允许“已验证的空集合”，但阶段本身不消失。归一采用二叉有界 fan-in；recipe digest 绑定 source revision、segment 范围、依赖和重试合同。Planner step 只映射 `plan_step_id` 展示分组，不参与 digest，也不改变 host DAG。5 项新 recipe 合同测试通过。A1 尚未接入 replacement Profile 或生产路由。 |
| 2026-09-12 | A1 确定性完成 | 新增 canonical v1 source scope、replacement Profile、Planner validator、Task admission 与隔离 composition。domain payload 只接受 `sourceRevisionId/commandId/segments`，旧字段和额外字段失败关闭；Run binding 固定 `novel_analysis/purra-native/v1/recipe v1`。admission 只允许 Planner 提供 MODEL analyze/review 展示步骤，host 继续独占 source scope、DAG 与 digest。versioned 生产组合根已安装该 Profile，但当前 lifespan rollout 仍只启用 Writing，因此新分析 Run 仍走 legacy；显式启用 Analysis rollout 的确定性路由测试通过。A2/A3 executor 尚不存在，Profile 不提供 dispatcher，误切流会明确失败而不会回落旧实现。recipe/profile/composition/routing 定向组 122 项通过。 |
| 2026-09-12 | A2 第一批 | replacement segment 新增必需 `sectionDigest`，recipe digest 因而同时绑定 revision、section 内容摘要和字符范围；Profile prepare 时逐 segment 校验 revision、section ordinal、digest 与边界，漂移失败关闭。新增 `listAnalysisSourceSegments` / `readAnalysisSourceSegment`，目录不带正文，正文被宿主切成连续 canonical `sourceSpanId`；提交证据只能由 `(segmentId, sourceSpanId)` 回查重建，伪造 offset handle 被拒绝。新增 normalize observation 的分页目录与按 ID 有界读取工具，正文和 evidence refs 不再被投影剥离后又无工具可读。工具指引与 unit read 授权由同一 capability manifest 生成并在 Profile 安装时校验。A2 尚未接入 Unit executor；相关定向组 128 项通过，冻结 194 文件一致。 |
| 2026-09-12 | A2 确定性完成 | 新增 host-owned canonical Unit context，Profile 工具目录按 unit kind 做最小授权：Root 无工具，extract 仅来源目录/片段，normalize、overview、distill 仅 observation 目录/详情，host-only Unit 无材料工具。observation 目录允许为空：事实存在而 observation 为空是合法分析结果，宿主不能强迫模型编造 observation；normalize 的证据覆盖仍由输出 validator 保证。该合同由同一 manifest 同时生成 enabled names 与模型指引，避免再次出现“提示要求工具但授权未开放”。A2 的材料和授权合同完成；实际 Unit 模型调用留给 A3 executor 做接线验收。 |
| 2026-09-12 | A3 第一批 | 新增基于 PurrA `ArtifactLifecycle` 与现有 SQLite Artifact/claim adapter 的 attempt-scoped Unit Artifact store。Artifact owner ref 使用完整 `taskId:unitId:attempt` Operation identity，同一 attempt 同 payload 可重放，不同 payload 冲突；下一 attempt 始终创建不同 Artifact，因此不再撞上一次 OPEN/FINALIZED 内容。模拟“append 已成功、finalize 前中断”后可复用同一批次继续 finalize，已完成旧 attempt 也不会阻止新 attempt 成为 Durable Unit 的 output winner。4 项恢复测试通过；尚未接 Unit executor、最终 review Artifact 与阶段播报隔离。 |
| 2026-09-12 | A3 Executor 合同 | 新增 v1 model Unit 严格输出 schema 和 Durable Unit executor。extract、normalize、overview、distill 的模型输出先做字段、长度、合并覆盖和 canonical evidence handle 校验；结构化输出或 evidence 无效均抛可重试模型错误。validate、coverage、review 由宿主执行，最终 review 强制包含独立 `storyOverview`、technique result、coverage report、evidence index 和 `pending_review` 状态。每个 Unit 只在验证通过后提交 attempt Artifact，stage/report 尚未进入该事务。完整七阶段 recipe 已用确定性 model runner 跑通。新增 PurrA Recipe dispatcher/descriptor，预算采用 PAUSE_RECOVERABLE，model attempt 上限来自 host recipe。实际 Provider operation runner 与提交工具仍待接入。 |
| 2026-09-12 | A3 Entry 第一批 | 新增 replacement request compiler，直接从持久化 source revision/sections 生成 canonical segment 列表；每个 segment 固定 section digest、ordinal 和连续字符范围，拒绝不存在、无 section、无 digest、空正文或过小隐式预算。该编译器不 import legacy service/source reader。当前 HTTP router 尚未选择 replacement service，Analysis rollout 继续关闭。 |
| 2026-09-12 | A3 Operation 接线 | 新增 replacement `submitNovelAnalysisUnitResult`，模型提交先经过当前 Unit schema、observation ID、canonical source handle 与 `(taskId, unitId, attempt)` Operation 身份校验，再写 attempt Artifact；非法提交保持 `NOT_STARTED`，不会留下候选。新增 PurrA Operation-backed model runner，provider lease 在所有终态释放，模型必须调用提交工具，成功后按 Operation owner 回读 finalized Artifact；Executor 对回读结果再次规范化校验并以幂等 replay 结算 Unit output。模拟 Provider 已完成 `readAnalysisSourceSegment → submitNovelAnalysisUnitResult → validated response → Artifact reload` 三轮链路。28 项 A3/Profile/Artifact 定向测试通过。Analysis rollout 仍关闭；下一步是完整 recipe 的 Operation fixture 与 A3 recovery/entry service。 |
| 2026-09-12 | A3 Recovery 第二批 | replacement Executor 新增 Durable failure classifier：缺少提交、结构化输出和 evidence handle 归入可重试 `MODEL_OUTPUT_INVALID`，Provider 暂时故障归入可恢复 `TRANSIENT_PROVIDER`，宿主合同错误保持不可重试 business invariant。若某 attempt 已 finalized 提交但在 Unit settlement 前中断，下一 attempt 会先对账并复制已验证结果成为新 winner，不重复调用模型；未 finalized 的旧 attempt 不会被采用。最终 review Unit 把 Artifact ref 写入 generic dispatcher 的 `finalResponse`。完整七阶段 recipe 已经由真实 PurrA Operation、按 Unit 最小工具集和模拟 Provider 跑通，形成包含 overview/coverage/technique 的 review Artifact；A3 recovery 尚需补取消、observer failure 与重启矩阵。 |
| 2026-09-13 | A3 Recovery 确定性完成 | 补齐取消、阶段 observer 失败、Provider 暂时失败、双分片覆盖和进程重启矩阵。取消发生在模型调用前不会产生 attempt Artifact；业务 Unit 完成后 observer 异常只暂停 Task，不回滚已完成输出；重启会把 `execution_recovery_after_restart` 的 finalized 旧 attempt 对账到新 winner，不重复调用模型。4 项 recovery matrix 已通过。 |
| 2026-09-13 | A3 Entry 第二批 | 新增 replacement-only execution service，把持久来源 revision 编译为 canonical request，并显式接入 `AgentRunService`、PurrA Operation model runner、Durable Unit executor、Run provenance 与 validated-result transaction。入口在 Analysis rollout 未启用时失败关闭，不回落 legacy。共享 saved-model binding 只持久化经本机配置精确核验的非密钥身份；Profile 把它带入 Task metadata。新增 replacement recovery service，严格校验 source Run implementation、recipe digest、Task 状态和 model binding，并区分原 task command 与新 continuation Run command。模拟 Provider 已通过真实 PurrA Root 入口完成 Planner→完整 recipe→validated review Artifact；A3 replacement 定向 49 项、相关组合/路由/生命周期/事件回归合计 193 项通过。生产 HTTP 路由和 Analysis rollout 仍未切换。 |
| 2026-09-13 | A3 Continuation Core | 用真实 PurrA continuation Core 完成“Provider 暂时失败导致 Task paused → 恢复健康 → continuation Root 接管未完成 Unit → Task completed”的纵切。恢复过程不重新调用 Planner；首个 Root 与 continuation Root 分别持久化 `created`/`continuation` Task binding，且 continuation 仍固定到 replacement implementation。 |
| 2026-09-13 | A3 Presentation 第一批 | 新增 replacement review Artifact、Run 列表和 SSE 的只读投影。只有 completed Durable Unit 的 finalized winner 可显示，losing attempt 即使 finalized 也被拒绝；新 Artifact 使用 `novel-analysis-v1://` 与 `purrtypos.novel_analysis.review.v1`，不写回 legacy schema。版本感知 SSE 在同一 cursor 查询新旧 namespace，避免分别分页后漏掉交错事件。前端可读取 story overview、原文引用、事实和技法，并隐藏尚未实现的 legacy 保存/技法审核控件，明确标记为待审核。生产创建、恢复、审核发布仍未切流。 |
| 2026-09-13 | A3 Write Review Contract | 新增 replacement-only 用户审核 Artifact 与 schema v4 正式发布服务。审核 command 以 source revision + command 形成幂等身份，同 command 不同内容失败；二次编辑产生新的 immutable reviewed Artifact，但始终保留原 settled winner 溯源。事实和技法卡只能保留原 review 中的 ID，证据必须精确匹配原章节、字符范围、引文与 digest；可发布 fact kind 在模型输出阶段即被 host 枚举约束。正式发布以内容 digest 幂等写入 `novel_source_analyses` 及 facts/cards/evidence，已用 continuation canon preview 验证下游可消费。旧 review/publish service 只处理 legacy Artifact，不参与 replacement 写入。生产创建/控制切流仍关闭。 |
| 2026-09-13 | A3 Product Control Entry | start 按新建 rollout policy 路由，pause/resume/cancel 按 Task 的 `created_by_run_id` 持久 implementation 路由；因此关闭 rollout 只停止创建新 replacement Root，不会把既有 replacement Task 误送 legacy 或阻断恢复。HTTP 产品入口已接入 versioned control service，默认 Analysis rollout 仍关闭。编辑后重跑在 replacement 合同完成前明确 409，绝不回落冻结执行器。本批测试同时发现并修正产品回执误查不存在的 `section_id` 字段。 |
| 2026-09-13 | A3 Replacement Follow-up | 新增 replacement 自有 reactive Follow-up Run：由绑定 Artifact 的 `createdByRunId` 决定实现身份，不受当前新建 rollout 开关影响；请求继续使用 canonical source scope，只开放读取绑定 review Artifact、列来源片段和读取来源片段三个有界 READ 工具。待审核与用户审核 Artifact 均校验 source revision 和 settled lineage，公共会话历史只读取同 session 的完整 replacement user/assistant turn。Run 投影记录 `interactionKind=follow_up` 与来源 Artifact；前端统一剥离 legacy/replacement URI 前缀。182 项分析纵切/legacy 回归、31 项前端行为测试和 TypeScript 检查通过。真实 Provider/Electron 仍未验收。 |
| 2026-09-13 | A3 Replacement Edit Replay | 编辑目标按持久 implementation 路由；legacy 目标仍由冻结服务处理，replacement 初始分析和追问分别重建自己的 planned/reactive Run。新 Root 持久化成功前不修改旧分支，`on_run_started` 才在独立事务中归档目标及同 session 后缀；预检失败、运行中后缀和跨会话目标均保持旧分支不变。追问重跑的历史严格截断在目标之前，且继续绑定原 review Artifact。replacement Run 投影已排除 superseded Run，避免数据库已归档但 UI 仍显示旧分支。关闭 rollout 后的编辑/恢复仍按原 Run 身份选择 replacement Profile。A3 至此达到确定性完成，真实 Provider/Electron 仍是独立门禁。 |
| 2026-09-13 | A3 Isolated Electron Gate | 新增 fail-closed 的进程级 Analysis 验收切流：生产默认仍只启用 Writing replacement；只有显式 `PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE=1` 且后端数据库目录存在专用 marker 时，才允许新 Analysis Run 进入 replacement。Electron 同时拒绝真实 `userData`，要求第二个 marked renderer 目录。`npm run acceptance:novel-analysis:electron` 会先确认 5174/18321 未占用，再创建两套独立临时目录并严格启动 Vite/Electron；退出后保留目录用于核验证据。Python 启动级与 Electron 进程管理测试均已覆盖默认关闭、缺 marker 拒绝、真实 userData 拒绝和显式验收开启。该项只完成安全验收入口，不等于真实 Provider/Electron 已通过，生产切流继续关闭。 |
| 2026-09-13 | A3 Isolated Electron Smoke | 使用新入口实际启动开发态 Electron，窗口加载严格端口 `localhost:5174`，后端 `/api/database/info` 指向新建临时数据库且内容为空。首次烟测发现正常关闭 Electron 时 `concurrently -k` 会因被终止的 Vite 返回失败码，已改为以第一个正常退出进程判定成功；复测 `⌘Q` 后启动器退出码为 0，5174/18321 均无监听。137 项 Analysis/路由/启动/冻结 backend 回归、10 项 Electron 进程测试、TypeScript 和 194 文件冻结校验通过。该烟测未配置模型、未发送 Provider 请求，只证明隔离启动与清理，不满足真实 Provider/UI 功能验收。 |
| 2026-09-13 | P1 Part Contract 第一批 | 在 `backend/agents/screenplay/` 新增 replacement v1 唯一 Part registry，统一 9 种 Part kind 的 completion contract、tool profile key、展示 key 和 attempt 上限；`episode_metadata` 明确映射为 host-captured Part，不再依赖旧 typo 字符串。新增 `ScreenplayPartOperationScope`，唯一单元身份为 `taskId:unitId:attempt`，不创建 synthetic RunBinding；每个 Part 必须显式携带 `sourceRevisionRefs` 与 `deliverableRevisionScope`，structure 也必须声明空 scope 而不是省略。18 项合同/实现身份/冻结测试通过，冻结 194 文件未变。P1 仍需把 registry 接入 replacement recipe、工具授权、输出 validator 与 Profile。 |
| 2026-09-13 | P1 Recipe/Profile 第二批 | 新增由唯一 Part registry 编译的 replacement recipe：Part kind 自动决定 max attempts、completion contract、tool profile 与 presentation key；编译器拒绝缺失 revision scope、重复 Part id、前向/未知依赖，并把完整 canonical Part 合同纳入 recipe digest。`screenplay.purra-native.v1` Profile 已安装到 versioned composition，只接受带 schemaVersion 的严格 Root scope并持久化 replacement identity；旧 payload 的附加字段不会被兼容吸收。生产 rollout policy 仍只启用 Writing，Screenplay create route 仍指向冻结 `screenplay`；显式测试 policy 才能选择新 Profile。266 项 backend 剧本/启动/持久化联合回归、19 项前端剧本测试、TypeScript 与 194 文件冻结校验通过。P1 下一步是从 registry 生成实际最小工具 manifest、Part 输出 validator 和 durable admission/dispatcher 接缝。 |
| 2026-09-13 | P1 Capability/Completion 第三批 | 唯一 Part registry 现在同时生成 recipe 内的精确 `toolNames`、模型指引和 completion evidence 合同。candidate-tool Part 才包含 `writeScreenplayCandidatePartV1`；host-capture Part 没有候选写工具；host-only Part 没有模型工具。漏写 Candidate 与缺失 host structured output 明确成为可重试模型输出错误，混用 candidate/hostCapture/hostResult 则是不可重试合同冲突。新增 replacement Durable Task descriptor/dispatcher 接缝，预算耗尽采用 `pause_recoverable`，executor id 固定为 `screenplay.purra-native`，Profile 只在提供真实 Unit executor 时创建 dispatcher。273 项 backend 剧本/启动/持久化联合回归、19 项前端测试、TypeScript 与 194 文件冻结校验通过。当前 `toolNames` 是已冻结的 v1 capability manifest，实际 read handler 与 attempt-scoped Candidate writer 尚未安装，因此 Screenplay rollout 继续关闭；下一批实现有界 read handler 与 Operation Unit context，Candidate writer 留在 P2。 |
| 2026-09-13 | P1 Bounded Read 第一批 | replacement Profile 安装首批真实 READ handler：Root 的 `inspectScreenplayProjectV1` 只返回当前项目元数据与 accepted Revision 目录；Operation 的 `readScreenplayBoundRevisionV1` 不接受模型提供 revisionId，只能按角色读取 Host `deliverableRevisionScope` 中冻结的 Revision，因此历史版本可浏览能力不会绕过正式证据基线。版本正文在 SQL 查询阶段即限制单 Part 12,001 字符、总返回 48,000 字符、最多 100 Parts；大型 payload/summary 返回结构化 omitted 标记，不截断 JSON 字符串。Root 当前只启用项目目录工具，Operation Unit context 尚未接入，因此冻结版本工具虽已具备 handler 但不会提前暴露。P1 下一步补齐 canonical Unit context 和 source/dependency read handlers；Candidate writer 仍属于 P2。 |
| 2026-09-13 | P1 Unit Context 第一批 | 新增严格 canonical Unit payload 与 `ScreenplayPartOperationScope.from_mapping`，要求 schema、project、task、unit、attempt、operationScopeId、Part kind/key、source refs 和 deliverable revision scope 字段完整且无额外字段；`operationScopeId` 必须精确等于 `taskId:unitId:attempt`。Profile 的 Unit ExecutionState 只携带 Operation scope，run binding attributes 不写 Part 身份，从结构上阻止 synthetic Part RunBinding 回流。工具 enablement 按 registry manifest 计算：host-only Unit 为零工具；任何模型 Unit 只要还有一个 manifest handler 未安装就整体失败关闭，不会用缩水工具集偷偷执行。首批 Root/Revision READ handler 已安装，source/dependency handler 与 Candidate writer 未齐，因此模型 Unit 仍不可运行，Screenplay rollout 保持关闭。363 项 backend 剧本/工具/启动/持久化联合回归、19 项前端测试、TypeScript 与 194 文件冻结校验通过，5174/18321 均无监听。 |
| 2026-09-13 | P1 完成 / P2 Artifact 第一批 | 完成 replacement Part 的受限数据面：原作来源通过 `sourceBookId + sourceItems(contentDigest)` 独立冻结，明确纠正旧 `sourceRevisionRefs` 实际主要指剧本 deliverable revision、不能冒充原作版本的错误前提；来源正文漂移即失败。直接依赖只能按 Unit 的 `dependencyPartKeys` 读取同 project/task 下已完成且 finalized 的 replacement Candidate。`draft_scene` 额外绑定唯一 `episodeNumber + sceneId`，场景表和已有草稿只能从 `deliverableRevisionScope` 指定的 immutable revision 读取，工具不接受 revision/scene 参数。P1 manifest 的全部 7 个工具 handler 已安装，Root/各 Part 按 registry 最小授权，P1 达到确定性完成。P2 新增 attempt-scoped Candidate Artifact 与 `writeScreenplayCandidatePartV1`：Artifact owner 是完整 `taskId:unitId:attempt` Operation，重试 attempt 使用新 Artifact；同 attempt 同内容可在 append/finalize 中断后恢复，同 attempt 改内容冲突；提交为 PurrA proposed、host-managed durable、cancellation-linearizable write，不能用于 host-capture/host-only Part。30 项 replacement 定向测试与 531 项剧本/组合/路由/持久化/冻结联合回归通过。Screenplay 生产 rollout 仍关闭；下一步是 P2 Operation Unit executor、漏写重试和 winner settlement。 |
| 2026-09-13 | P2 Unit Executor 第一批 | 新增 candidate-tool Part 的 Durable Unit executor。Executor 从 recipe/task/unit 元数据重建唯一 `ScreenplayPartOperationScope`，把 registry 生成的最小工具集交给 model runner，并且只接受属于当前 project/task/unit/attempt 的 finalized Candidate 作为 `output_ref`；任意其他 Artifact 无法冒充 winner。漏写 Candidate 和 Candidate scope 无效被分类为可重试 `MODEL_OUTPUT_INVALID`，Provider 暂时错误归入可恢复外部错误，合同冲突仍不可重试。recipe 现把上游 Unit id 映射为稳定 semantic `dependencyPartKeys`，读取层不依赖偶然 unit id。若 attempt 1 已 finalized、但在 settlement 前因中断或重启丢失回执，attempt 2 会先回读旧内容并复制为当前 attempt Artifact，模型不会重写，且 winner ref 仍精确指向当前 attempt。33 项 replacement 定向测试与 534 项剧本/组合/路由/持久化/冻结联合回归通过。该批尚未实现真实 PurrA Operation model runner、host-capture Artifact 和 host-only executor，Screenplay rollout 保持关闭。 |
| 2026-09-13 | P2 Operation Model Runner | candidate-tool Part 已接入真实 PurrA `execute_operation` 路径：每次模型执行在 owning Root Run 内运行，不创建 Part Run，也不传递无法落库的 synthetic `RunBinding`；Operation identity 必须与 `taskId:unitId:attempt` 完全一致。响应 validator 强制观察到 `writeScreenplayCandidatePartV1` 的 committed Artifact receipt，完成后再按当前 scope 从 SQLite 回读，不能信任模型文本。Provider health lease 在成功和抛错路径均释放，usage/tool events 继续归 owning Root。模拟 Provider 已完成工具提交与最终确认两轮，并验证 Root binding 未被 Part 覆盖。接线测试同时发现 Profile 的 `max_model_rounds=1` 会让正常工具回合必然失败，现改为有界 6。另根据 `SqliteLongTaskRepository.claim_unit` 的实际 `attempt = attempt + 1` 语义，纠正“首次执行 attempt 为 0”的错误前提：Operation scope 现在只接受正整数，恢复测试使用真实 1→2。34 项 replacement 定向测试与 535 项联合回归通过。生产 Screenplay rollout 仍关闭；下一步实现 host-capture Artifact 和 draft_scene/episode_metadata structured runner。 |
| 2026-09-13 | P2 Host Capture | 新增 replacement 独立的 `host_capture_attempt` Artifact，`draft_scene` 只接受与 Host scope 一致的 `{sceneId, sceneText}`，`episode_metadata` 只接受一致的 `{episodeNumber, title, continuitySummary}`；字段缺失、额外字段、空正文和跨 scope 身份均失败。两类 Part 使用 JSON-object 模型能力、PurrA Operation response validator 与最多 6 回合的修复循环，最终输出由 Host 再次解析验证后才写 Artifact。Artifact 继续以正整数 attempt 的完整 Operation 作为 owner，同 attempt 同内容重放、改内容冲突，1→2 中断恢复先复制已验证 capture 而不重调模型。写入前后均检查取消；写后取消留下可对账 Artifact，不伪装 Unit 已结算。本批额外纠正异常分类：`screenplay_structured_output_invalid`、缺少/无效 Candidate、截断输出属于可重试 `MODEL_OUTPUT_INVALID`，不能因 `ModelGatewayError.retryable` 被误归为 `TRANSIENT_PROVIDER`。38 项 replacement 定向测试与冻结校验通过；此前同批 539 项联合回归通过。Screenplay rollout 继续关闭。 |
| 2026-09-13 | P2 Host-only Executor | `host_projection`、`validation`、`final_response` 现在由纯 Host executor 处理，不调用模型。每个结果写入独立 `host_result_attempt` Artifact，并记录精确的上游 semantic Part key、winner URI 与内容 digest。Executor 同时核对 Unit 声明的 `dependencyPartKeys`、Durable Task 数据库中 completed `output_ref` 和 PurrA 传入的 `dependency_outputs`，三者不一致即失败；Candidate、Host Capture、Host Result 三类依赖分别校验 namespace、project/task owner、finalized 状态和 digest。`host_projection` 当前只产生明确的 `projectionStatus=staged` 收据，不写产品 Revision，避免把 P4 尚未实现的投影伪装成成功；`final_response` 必须消费同 Task 的 validation Artifact 且 `valid=true`，最终由其 Artifact URI形成 Durable Task `finalResponse`。39 项 replacement 定向测试与 540 项剧本/组合/路由/持久化/冻结联合回归通过。Screenplay rollout 继续关闭；下一步用 replacement dispatcher 运行完整 DAG，再进入 P3/P4 的正式 Revision 投影。 |
| 2026-09-13 | P2 Full Recipe Fixture | replacement Profile 不再是无 admission 的 single-pass 空壳：Root 必须携带闭合、版本化、宿主生成的 `screenplayRecipe`，Profile 进入 staged planning，并只允许 Planner 映射可见 MODEL 步骤，实际 DAG、目标角色、revision scope 与预算仍由 Host 独占。完整 6-Part 链已通过 `admission → dispatcher → document candidate → draft scene capture → episode metadata capture → host projection → validation → final response`。测试同时发现 PurrA public recipe compiler 会以 `step.id` 生成 Durable Unit `semantic_key`；现以 canonical Part key 作为 step/unit identity，本地 recipe id 只留在元数据，修复 host-only 阶段无法按语义 key 找到 winner 的问题。恢复矩阵覆盖漏写 Candidate、Provider 暂时失败、显式 Durable 取消和 finalized 后崩溃恢复；44 项 replacement 测试与 595 项联合回归通过，P2 达到确定性完成。Screenplay 生产 rollout 仍关闭。 |
| 2026-09-13 | P3 Revision Receipts 第一批 | 新增 attempt-scoped `screenplay_operation_access_receipts`。只有实际成功读取冻结剧本 Revision、原作章节或已结算依赖后才写 receipt；越界、digest 漂移和读取失败不留记录。receipt 绑定 `operationScopeId/project/task/unit/attempt/rootRun`、资源 ref、内容 digest 与最小元数据；重复读取幂等，身份或 digest 改变失败关闭。Unit settlement 将有序 receipt 清单及聚合 digest 写入 validation receipt，host-only 依赖消费同样留痕；崩溃后复制已完成 Candidate/Capture 时会把上一 attempt 的输入 receipts 带上明确 lineage 复制到新 winner。当前为确定性接线，尚需用真实 PurrA Operation 工具回合验证 read receipt，再完成 P3。 |
| 2026-09-13 | P3 Revision Receipts 完成 | 模拟 Provider 已在真实 PurrA Operation 内执行 `readScreenplaySourceItemV1 → writeScreenplayCandidatePartV1`，模型第二轮实际收到原作工具结果，Unit winner 的 validation receipt 与持久 access receipt 对 operation scope、URI、digest 完全一致。场景读取同时记录 sceneList/draft 两个 Revision，依赖读取记录唯一 winner；来源 drift 后拒绝且不新增 receipt。投影前会逐 Unit 对账 validation receipt 与数据库行，receipt 被删除或篡改都会失败关闭。P3 达到确定性完成；真实 Provider 与 Electron 仍未执行。 |
| 2026-09-13 | P4 Revision Projection 第一批 | `host_projection` 不再返回虚假的 staged 状态，而是在 cancellation-linearizable 事务中把全部直接模型 Part winner 组装为 immutable candidate Revision；剧本草稿按 episode/scene 形成 document + episode Parts，其他角色形成 document snapshot。Projection 只消费 P3 已核验 receipts，Revision inputs/source refs 来自实际读取而非“允许读取”清单；输入 Revision 必须仍是当前 Head，模型读后 Head 漂移会使投影失败且不落半成品。投影 receipt 以 task 作为副作用幂等键，若 Revision 已提交但 Unit settlement 中断，下一 attempt 复用同一 Revision，再生成新的 attempt host-result Artifact。validation 现在只接受 committed Revision，final response 传递 revisionId；候选 Revision 不自动接受为 Head。项目删除同步清理 replacement receipts 与 `purrtypos.screenplay.v1` Artifact。P4 尚未完成 Operation usage 聚合、生产 request compiler/entry 接线、真实 Provider/Electron 与历史回放验收，Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Operation Usage | 新增以 `taskId:unitId:attempt` 为主键的不可变 Operation usage receipt，从 Root Run journal 中只读取匹配 `executionScopeId` 的 `model.call_recorded` 与 `context.usage_recorded`；并行 Part、失败重试和 continuation Root 不再互相覆盖。所有模型 Part 结算后，`host_projection` 在写 Revision 前按 Root Run 聚合，并通过 PurrA `LongTask.record_usage` 对每个 Root 只结算一次；重放只接受完全相同的聚合值。Provider 未回报 usage 时保留调用次数并把 reasoning 标记为 unknown。原始模型异常优先，usage/lease 附属错误只写诊断，不能覆盖原始异常。任务最大 invocation 预算从错误的 Durable attempt 数修正为 `Part maxAttempts × Operation maxModelRounds`。新增并行、1→2 重试、跨 Root、未知 usage、不可变冲突和真实 Operation 事件归属测试；replacement 联合回归 599 项、compileall、diff check 与 194 文件冻结校验通过。P4 下一批为生产 request compiler/entry 接线，Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Request Compiler / Entry 第一批 | 新增 replacement-only Host request compiler 和 Root execution service。产品 `ScreenplayStageCommand` 先由 Host 查询 active project、当前 Head 与原作 scope，再冻结成闭合 `ScreenplayHostRecipeSpec`；recipe/Part 增加规范 `to_mapping`，不再由调用方手拼 metadata。首个完整纵切支持 `sourceAnalysis/create`，并支持基于 accepted `sceneList + screenplayDraft` 的整体验收 `review` recipe；尚无 episode-shaped Revision projector 的 `sceneList`、`screenplayDraft`，以及现有工具无法读取依赖版本的 `creativeBrief`、`structure` 均明确失败关闭，非 `current_stage` scope 也不会被静默忽略。入口强制 create policy 选中 Screenplay replacement、保存无密钥 runtime binding，并直接接入 `AgentRunService + PurrA Operation runner + replacement Unit executor`；关闭 rollout 时不可构造。兼容当前 Screenplay wire 未携带 `modelConfigId` 的事实，仅在 Provider/模型/端点/能力/密钥唯一匹配一个 saved config 时反查绑定，歧义即拒绝。模拟 Provider 已从正式 Root 完成 Planner→原作读取→Candidate→usage→Revision，且无 Head 自动接受。联合回归新增 8 项入口/编译测试；完整 Screenplay replacement/旧实现联合门禁继续通过。生产 HTTP 尚未切换，因为新 Root 尚缺 `screenplay_agent_turns` 的 replacement 会话投影；Screenplay rollout 继续关闭。下一批先实现 episode-shaped recipe/projector 与 replacement 会话投影，再切 HTTP。 |

| 2026-09-13 | P4 Episode-shaped Recipe / Projection | Host compiler 已按 accepted upstream Revision 推导 `sceneList` 的 episode Part，以及 `screenplayDraft` 的逐 scene capture + episode metadata Part；`current_stage`、显式 episodes、next episodes 与 all remaining 均从冻结版本计算，空集和缺失 episode/scene 身份失败关闭。`document_section` 的能力合同扩展为 Host scope 约束下的 source/revision/dependency 有界读取；候选 validator 强制读取每个必需上游 Revision，scene-list 提交工具同时校验 episodeNumber、title、非空且唯一的 scene id。Projection 新增 scene-list document + episode 快照，局部 scene-list/draft 候选会继承已接受 baseline 中未修改的 episode；baseline 必须仍是当前 Head，并作为正式 revision input 和 projection digest 的组成部分。新增 episode 编译、候选形状、必需读取、scene-list 投影与局部继承测试；60 项 replacement 定向测试与 628 项联合回归通过。生产 HTTP 与 Screenplay rollout 仍关闭；下一批只实现 replacement 会话投影和正式入口纵切，不复用 legacy executor。 |
| 2026-09-13 | P4 Replacement Conversation Projection | replacement 目录新增原子 Turn/Operation admission、Root begin/terminal projector、会话 snapshot 与 canonical output 查询。Host recipe 与无密钥 runtime binding 在 admission 时冻结；Root begin 与产品 running 状态、Root terminal 与 Operation/Turn/Task/Revision attachment 分别在同一 PurrA 事务中提交。DONE 必须存在唯一 completed LongTask、replacement projection receipt 与匹配 Revision；缺任一凭据会回滚 Root terminal。模拟 Provider 已从正式产品命令跑通 Turn→Root→Planner→Operation→Revision→Turn completed，运行中 snapshot 也能按 Root 找到尚未 terminal attachment 的 Task。 |
| 2026-09-13 | P4 Replacement Cancellation 第一批 | replacement Turn 取消先原子持久化 Turn、Operation、LongTask 栅栏，再交由 PurrA Run cancellation plane 终结 Root；排队且尚无 Root 的 Turn 可在同一事务直接取消，运行中取消不依赖进程内 task registry，直接按 Root 发起的取消也由版本路由 projector 补齐产品栅栏。只有存在显式产品取消栅栏的 CANCELED Root 才投影为 canceled；意外 CANCELED 归 failed，paused LongTask 仍归 paused；对已完成 Turn 的取消不会污染历史状态。幂等 cancel command 冲突失败关闭。71 项 replacement 定向测试与 639 项剧本/组合/路由/持久化/冻结联合回归通过。恢复 continuation、重启 orphan 对账、truncate 和正式 HTTP 切换尚未完成，因此 Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Replacement Resume / Restart 第一批 | paused Operation 现在从原 Root 的持久 ExecutionPlan、Operation 中冻结的 Host recipe、LongTask recipe digest 和无密钥 saved-model binding 构造 PurrA `DurableTaskContinuation`；实现 identity 补齐 recipeVersion=1，恢复时按原 Root 的持久 identity 路由，不重新执行 create rollout 决策。continuation 先用 CAS 独占 Turn，再恢复 LongTask，新的 Root 原子替换 Turn 当前 Root 并把同一 Operation/Task 置回 running；竞争恢复失败不能 pause 获胜请求。模拟 Provider 已验证首次容量故障令 Task paused，随后新 Root 复用同一 Task 完成 Candidate、Revision 与 Turn。重启恢复区分首次提交的过期 pre-Run claim（回 queued）和 continuation 的过期 pre-Run claim（Task/Turn 回 paused）；已有 Root orphan 继续由 PurrA 通用恢复负责。lifespan 按 rollout identity 单选旧或新 Screenplay 启动恢复，避免旧 repository 把 replacement Turn 标成永久失败。75 项 replacement 定向测试与包含 lifespan 的 654 项联合回归通过。resume command receipt 的网络重放、truncate 和 HTTP 切换仍未完成，Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Replacement Resume Receipt / Truncate | replacement continuation 现在先在 `screenplay_agent_operation_commands` 持久化唯一 resume reservation；同一命令同一请求只允许一个 dispatch，未过期重放返回已存回执，过期 starting reservation 可按 epoch 接管，命令复用为不同请求则 409。新 Root begin 在同一事务校验 reservation/owner/epoch/identity digest 并写入 bound Root，terminal commit 同事务把回执更新为 succeeded/failed/paused/canceled；完整模拟 Provider 已验证完成后的网络重放不创建第三个 Root。replacement truncate 不调用 legacy repository：先逐 Turn 写取消栅栏并交 PurrA cancellation plane 终结，随后要求 Turn、Root 树、LongTask/Unit 全部越过终态屏障才删除；外部有效 Root 租约会返回 409 并保留全部行。删除时清理 replacement receipt、artifact、Task 与 Operation 数据，保留审计 Run 但解除 session 流绑定；未接受候选可删除，已接受或当前 Revision 保留并解除已删 Task 引用。78 项 replacement 定向测试与 631 项当前联合回归通过。普通 HTTP 入口尚未切换，Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Replacement HTTP Single Path 第一批 | screenplay v2 conversation router 现在按进程级 implementation rollout 做一次明确选择：选中 Screenplay replacement 时，submit/snapshot/SSE/cancel/resume/truncate 全部使用 replacement service/query，不在请求失败后回落 legacy；未选中时保持冻结链路，供普通数据目录继续运行。新增独立的 Screenplay acceptance 环境开关与数据目录 marker，单独的环境变量不能误切真实用户库。replacement service 补齐 HTTP 所需的后台 Turn dispatch、resume 预留后 202 返回与持久 reservation 后台续跑接口；SSE 与 snapshot 使用同一 rollout 选择。正式 stageCommand 已接通；没有 stageCommand 的普通自由聊天当前明确返回 409，避免静默借用 legacy Planner，因此生产 rollout 仍关闭。HTTP 重放可能触发的重复初始 dispatch 由每次执行唯一 owner 的 CAS 隔离，失败者不能再把获胜 Turn 标成 failed。80 项 replacement 定向测试，连同 HTTP、rollout、组合、旧剧本回归、持久化与冻结检查共 658 项联合回归通过。 |
| 2026-09-13 | P4 Replacement Ordinary Conversation Contract | 无 stageCommand 的请求现在进入显式 `interactionKind=ordinary`：同一 replacement Profile 使用 Reactive Root、项目只读工具与直接文本响应，不经过 Planner，也不会触发 Task admission。ordinary Turn 独立 admission，不创建 Operation；Root begin/terminal/cancellation projector 分别只更新该 Turn，成功文本写入 assistantContent，取消通过 PurrA cancellation plane 终结。上下文只继承同项目、同会话、已完成的 ordinary replacement Runs，并受 conversation input 预算裁剪；系统约束要求项目事实先调用 `inspectScreenplayProjectV1`，禁止声称创建、修改或接受交付物。模拟 Provider 已验证只读工具调用与最终回答，且 Operation、LongTask、Revision 均未产生；queued 与 running ordinary Turn 的取消均通过。84 项 replacement 定向测试及 662 项当前联合回归通过。当前只读能力限于项目元数据和已接受版本目录，是否需要读取版本正文交由 live acceptance 的真实问题集决定，不提前扩权。 |
| 2026-09-13 | P4 Screenplay Live Acceptance 第一轮 | 新增 Screenplay 专用隔离 Electron 启动器；Electron 后端与 renderer `userData` 对 Novel Analysis/Screenplay 两种 replacement gate 使用同一 fail-closed 隔离规则。真实 GLM-5.3-Flash 普通问答 Run `run_018109a0c6c649d7` 在 19 秒内完成，实际调用 `inspectScreenplayProjectV1`，准确返回合成原创短片的名称、形态和设想；持久检查确认 0 Operation、0 LongTask、0 Revision，identity 为 `screenplay/purra-native/recipe 1`。正式“生成创作简报”先暴露两项确定性接线缺陷：原创项目被错误要求 accepted `sourceAnalysis`，以及合法空 `sourceItems` 被目录工具当成异常；均以最小修复和回归覆盖。随后 Planner 已能通过并进入 Durable Task，但下一次真实调用两次返回缺少最终外层 `}` 的 JSONL，PurrA 解析器复放确认为 `invalid JSON record`，Run `run_b94bc846876d49c5` 以可恢复 `invalid_planning_stream` 失败。故本轮只确认 ordinary live gate；正式 Operation→Revision、取消/恢复/truncate/历史回放仍未通过，生产 Screenplay rollout 保持关闭。 |
| 2026-09-13 | P4 Host Planner / Formal Live Vertical Slice | 纠正了“让 Provider Planner 重建 Host 已经闭合的 TaskSpec”这一职责倒置：Screenplay formal Profile 改用确定性的 `ScreenplayHostPlanner`，只把 Host recipe 映射为 3 个展示步骤，不调用模型，也不改变目标角色、DAG、revision scope 或预算；ordinary 对话仍走 Reactive Root。真实 GLM-5.3-Flash Run `run_61f695cc284a4948` 在隔离 Electron 中完成 3/3 展示步骤、4 次 Operation 模型调用和 4 次工具调用，形成候选 Revision `sprev_c47c3d8b99944006b68d7cc382889ba8`，且无 Planner 模型调用。应用 Revision 的服务端事务已原子更新 `creativeBrief` Head，项目推进到结构设计，界面显示 1 份当前文档、0 份待应用。该纵切同时暴露 replacement projector 把 `summary.proposalKind` 错写为内部标签 `replacementAgentCandidate`，导致服务端应用成功后前端投影抛错；已改为按 role 写现有公开 kind（如 `creative_brief` / `scene_draft`）并补回归，隔离验收数据按新合同校正后 UI 正确回放。正式 Operation→Revision→Head 纵切通过；取消/恢复/truncate/历史回放仍是独立 live gate，生产 Screenplay rollout 继续关闭。 |
| 2026-09-13 | P4 Screenplay Live Cancellation / Resume | 在同一隔离 Electron 项目完成两条真实控制面纵切。运行中取消 Run `run_138c0100951d464e` 后，Turn、Operation、LongTask 与 Root 均进入 canceled，并共享同一取消 receipt；structure Revision 数仍为 0，界面明确显示“已终止”。随后用隔离库中两个短期 Provider capacity lease 制造可恢复 `provider_capacity_limited`（不是伪造 Provider 端点或真实上游故障）：Root `run_2766bff9ddd14ae8` 将同一 Task `longtask_4fe8d1b7dbc6448a805b5264f9c223cf` 投影为 paused；释放验收 lease 后从 Electron 点击继续，continuation Root `run_31e4e68d0ed149ec` 复用原 Task 并以真实 GLM-5.3-Flash 完成 4 次模型调用、5 次工具调用，生成 structure Revision `sprev_ce60351797e64270801552a5ccd91685`。live 回放同时发现 canceled source Root 的事件会污染 continuation 消息，并让诊断页把 Turn ID 误作 Root；现只允许 authoritative Root 事件进入当前 assistant replay，并把同 Turn 的诊断 Run 统一重绑到最新 Root。刷新与完整进程重启后，UI 均显示恢复任务“已完成”、1 份当前文档 + 1 份待应用，诊断 Root 为 continuation Run。取消/暂停/继续/重启回放 gate 通过；structure 候选保持待应用，未为验收顺带修改项目 Head。 |
| 2026-09-13 | P4 Screenplay Live Truncate / History Replay | 在 SQLite backup API 备份后的隔离验收库中，截断接口先以外部活跃租约返回 409 且完整保留 Turn、Operation、Task 与候选 Revision，释放租约后再原子删除最新未应用 structure 回合及其 Operation、Task、Artifact 和候选 Revision；已接受的 creativeBrief Head/Revision 保持不变。live 验收发现续跑后 Turn 的 `planner_run_id` 只指向 continuation Root，截断会漏掉同一 Turn 事件仍引用的 superseded source Root，导致 snapshot 已删除而 SSE 继续重放旧事件。截断服务现从 `ai_agent_run_events.turn_id` 收集全部相关 run/root，统一纳入活跃屏障并从会话解绑；新增回归覆盖旧 Root 租约阻断、释放后双 Root 解绑和 SSE 无残留。修复后 snapshot 为 6 个 Turn、SSE 322 chunks 且已删除 Turn/Operation/Task/Revision/两个 Root 均无引用；Electron 完整重启后历史停在已取消回合，项目文档稳定显示 1 份当前、0 份待应用。该 gate 通过，生产 rollout 仍未开启。 |
| 2026-09-13 | P4 Screenplay Rollout Preflight / Packaged Acceptance | 重新生成 `build-resources/backend` 后，539 个应打包 Host 源文件与工作区逐文件一致，实际 macOS directory package 包含 `screenplay.purra-native.v1` 及全部 replacement 模块；包内 PurrA 从 `Contents/Resources/backend/purra` 加载，版本 1.0.0、wheel SHA-256 `9a3695b9fa2f37bc11b140c0077c5c1273c5b352fc2e13579af99ff859a03c2c` 与 candidate manifest 一致，签名后的 grpc/jiter/numpy/pydantic native modules 均可导入。未发布的本地包使用独立 `--user-data-dir` 启动，`/health` 正常且数据库明确位于 `/private/tmp` 空目录；32 项 package/rollout/lifespan 测试通过。预检同时确认当前默认 policy 仍只有 Writing，且 R5 的“新项目全剧本生成通过”尚无真实 Provider 证据：现有 live 证据只覆盖 ordinary、creativeBrief 与 structure，没有覆盖 sceneList、screenplayDraft、review/完成态。因此不能把打包成功误判为全部 live gate 通过，也不能现在开启生产 Screenplay rollout；下一项先补剩余角色的隔离全流程验收。该 directory package 是本地未公证验收产物，不是发布证据。 |
| 2026-09-13 | P4 Remaining Roles Live Gate（完成） | 同一隔离原创短片项目已用真实 GLM-5.3-Flash 完成并应用 creativeBrief `sprev_62983e7664fd404786b9fe10a9767730`、structure `sprev_3fa1bae0f638475faf8f19c2019c48a9`、sceneList `sprev_049b81c060504860a9db71e5bdd8ceac` 和首版 screenplayDraft `sprev_ec139d5113524fa084b09e8d9b28821e`。正文成功 Root `run_5f161dcaabc647b7` 的 6 个 scene capture、episode metadata、projection、validation、final 共 10 个 Unit 均 attempt=1 完成，界面推进到审阅修订。live 过程发现并最小修复短片 structure wrapper、Operation scope 泄露、共享 Root/SQLite 并发锁、host capture 依赖读取、模型输出错误分类和 Review 必读角色提示。最终 Review 又暴露通用 document wrapper 不满足产品 `inputContractVersion/reviewedDraftId/verdict/issues` 契约；现由模型候选合同、候选校验与 Host 投影共同形成 authoritative review，缺省 severity 按既有产品域规则规范为 `minor`。真实 Review Root `run_a4db52a48e394586` 完成并形成 v2；用户裁决后，修订 Root `run_303aebc0b7484290` 生成并应用 screenplayDraft v2 `sprev_113d0798ba8d4c4a8dbe7142ef230a3f`；二次 Review Root `run_8643b0130c164eae` 形成并应用 review v3 `sprev_2f730ca2b2c746128f5b9dc2e34d6b11`，其持久内容为 contract v2、精确绑定当前 draft、verdict `revise` 和 5 条 issue。为验证终态而非伪装模型无意见，5 条剩余意见以明确“隔离验收接受风险”用户裁决记录，随后 UI 完成定稿；数据库为 `active_stage=completed`、`completion_source=user`、revision 12，finalization event 精确引用上述 draft/review，Provider 活跃 lease 为 0。另修复“处理审阅意见”主按钮误发 ordinary Agent 对话的问题，改为直接打开本地裁决面板。P4 隔离全流程真实 gate 完成；生产 Screenplay rollout 仍保持关闭，下一项是独立 rollout/rollback。 |
| 2026-09-13 | P4 Screenplay Production Rollout | 普通数据目录的新 Screenplay Run 现在默认选择 `screenplay/purra-native/v1`，`PURRTYPOS_SCREENPLAY_LEGACY_ROLLBACK=1` 只把后续新建请求切回冻结实现；隔离 acceptance flag 继续要求显式数据目录 marker，且与 rollback 同时设置时失败关闭。`screenplay_agent_turns` 新增持久实现标记：历史行迁移为 `legacy-frozen-2026-09-12`，replacement admission 显式写 `purra-native`。启动时不再按当天 create policy 二选一恢复器，而是同时运行带实现过滤的 recovery；因此 rollout/rollback 都不会让旧恢复器把 replacement Turn 标成永久失败，也不会让 replacement recovery 接管 legacy Turn。默认路由、回滚路由、schema migration 和双向恢复隔离均有确定性覆盖。下一项是独立 rollback drill，不触碰真实创作数据。 |
| 2026-09-13 | P4 Screenplay Rollback Drill（完成） | 临时数据库演练显式启用 `PURRTYPOS_SCREENPLAY_LEGACY_ROLLBACK=1`：同一进程的新建 Screenplay 路由回到 `legacy-frozen-2026-09-12`，演练前已持久化为 `purra-native` 的 Run 在 resume/cancel/replay 时仍解析为 replacement，不受 create policy 变化影响。lifespan 在 rollback 下可正常启动；replacement recovery 忽略 legacy Turn，legacy recovery 也忽略 replacement Turn。演练未读取或修改真实创作内容，也未发布或部署产物。下一阶段不能直接删除 frozen legacy：必须先完成持久 legacy Run 只读盘点和稳定观察窗口。 |
| 2026-09-13 | P4 Legacy Retirement Preflight（观察 1） | 2026-09-13 23:28 CST 对当前桌面数据库执行只读元数据盘点：Screenplay Root/Child 范围内 running/paused/blocked Run 为 0，持久历史共 316 个未版本化 legacy 终态 Run（201 done、56 canceled、59 failed）；产品层 active Turn、active Operation、active `purrtypos.screenplay` LongTask 均为 0。该结果只是第一个时间点，不等于稳定观察窗口，也不授权删除旧实现；当前可以继续默认 replacement 新建入口，但 legacy retirement 仍需后续独立复核。盘点没有读取 prompt、正文、Revision 或其他创作内容。 |
| 2026-09-14 | P4 Screenplay Rollout Packaged Adoption（完成） | 从当前工作区重新生成 `build-resources/backend`，539 个应打包 Host 源文件逐一与工作区一致；包内 PurrA 及三个 Provider adapter 均从 `Contents/Resources/backend` 加载，PurrA 版本 1.0.0，四个候选 wheel 的 SHA-256 与 `purra-candidate.json` 全部一致。重新构建并 ad-hoc 签名 macOS arm64 directory package，启动后数据库明确位于 `/private/tmp/purrtypos-packaged-rollout-G8Pc50/purrtypos.db`。默认模式创建 Root `run_501d5f645fe945dc`，Turn/Run identity 均为 `screenplay/purra-native/v1/recipe v1`；同一隔离数据库以 `PURRTYPOS_SCREENPLAY_LEGACY_ROLLBACK=1` 重启后，新 Root `run_72fca715ce474d9a` 为 `legacy-frozen-2026-09-12`，前一个 replacement Root identity 未被改写。两次调用都使用无服务的本机端点并按预期快速失败，没有请求外部 Provider、没有产生候选 Revision，也未触碰桌面真实作品库。包运行后 `codesign --verify --deep --strict` 仍通过；36 项 Electron 测试、77 项 package/rollout/lifespan/routing/schema 回归、TypeScript、194 文件冻结和 diff check 均通过，5174/18321 无监听。该产物是本地未公证 directory package，不是发布或安装证据。 |
| 2026-09-14 | P4 Screenplay Legacy Retirement Preflight（观察 2）与新建回退移除 | 第二时间点以 SQLite `mode=ro&immutable=1` 复核真实默认持久库：316 条 legacy Screenplay Run 仍全部终态（201 done、56 canceled、59 failed），关联 Task 为 8 completed、7 failed、1 canceled，running/pending/paused/blocked 均为 0；未读取 prompt、正文或 Revision。真实库没有 production replacement 样本，因此另以无 `PURRTYPOS_SCREENPLAY_REPLACEMENT_ACCEPTANCE` 的最终包后端和全新普通隔离库 `/private/tmp/purrtypos-screenplay-production-preflight-xuSC65` 从正式项目/会话/Turn API 创建最小问答 Root `run_1fd930ca5fda4d2c`。真实 GLM-5.3-Flash 3 次请求完成，持久 identity 为 `screenplay/purra-native/v1/recipe v1`，准确回答项目标题，0 Operation、0 Revision、0 Provider lease。两个观察点和普通入口证据满足后，删除 `PURRTYPOS_SCREENPLAY_LEGACY_ROLLBACK` create policy 及 router 的 `ScreenplayAgentService`/`ScreenplayCanonicalOutputQuery` fallback；Screenplay HTTP 新建和回放入口现无条件使用 replacement service/query。历史 rollback drill 仅保留为过去证据，不再代表现行能力。旧 executor/profile/projector 文件仍被冻结，待下一项按历史读取边界分批移除。 |
| 2026-09-14 | P4 Screenplay Frozen Runtime Boundary Removal（完成） | 生产 `composition_factory.py` 已停止导入和安装 legacy Screenplay profile、candidate/root commit projector、continuation begin projector、checkpoint claim guard 与 cancellation projector；Screenplay namespace 在 versioned registry 中只剩 `screenplay.purra-native.v1`，但 legacy implementation identity 仍保留在持久路由注册表中用于识别历史记录。冻结专项测试改接 `tests/support/legacy_screenplay_composition.py`，该夹具不进入打包资源，避免测试依赖迫使产品组合继续双接线。replacement query 新增终态 legacy Turn 回放 fixture；重建后的最终 `.app` 又从包内模块在全新临时库读取 `legacy-frozen-2026-09-12` Turn，完整返回 completed 状态和旧 Assistant 文本。正式路由、replacement、旧专项隔离回归、compileall、194 文件冻结校验与 diff check 通过；package 源文件与工作区一致，PurrA 1.0.0 四个 wheel hash 一致，应用签名及 DMG 校验通过，5174/18321/18322 无监听。完整 `test_agent_refactor_boundaries.py` 仍有本轮之前已存在的 replacement `planner_run_id` 存储角色/输出 alias 守卫欠账，本项只新增并通过 production composition 禁止导入 frozen runtime 的精确守卫，没有借机放宽旧守卫。下一步应先清理这项列别名边界和盘点 frozen Screenplay 文件的非测试调用者，再决定删除批次；本轮未删除冻结源码。 |
| 2026-09-14 | P4 Screenplay Frozen File Deletion Boundary（第一批） | replacement Screenplay 对旧物理列 `planner_run_id` 的全部读取均在 SQL 中显式 `AS root_run_id`，Python 映射与公开输出只使用 `root_run_id/rootRunId`；完整架构守卫恢复通过，没有用扩大豁免掩盖旧名传播。调用图盘点同时发现 `main.py` 仍在启动时执行 legacy Turn 恢复，现已删除该写路径，只保留 replacement stale-admission recovery，并新增启动边界守卫。63 个冻结 Screenplay 路径中仍有四类生产依赖：现行 replacement/request schema 复用旧 `ScreenplayStageCommand`，项目与 Revision 路由使用 `screenplay_v2_service`，开发诊断使用旧工具标签只读投影，旧 operation repository/finalizer 仍引用旧 operation/part Artifact 类型。删除边界与顺序已记录在 `docs/migrations/2026-09-14-screenplay-frozen-file-deletion-boundary.md`；下一批先抽出现行 StageCommand 合同，不直接删除混合承担现行业务职责的旧 domain/service。 |
| 2026-09-14 | P4 Screenplay Current Command Contract Extraction（完成） | `ScreenplayStageCommand` 以及 command 专用 action/scope 类型已迁入 `agents.screenplay.contracts`；replacement conversation、entry、Host recipe compiler 和公开请求 schema 全部切到现行合同，wire 字段、round-trip 与既有 Host 校验错误保持不变。旧 Planner 的 `ScreenplayIntent`/TaskSpec 编译职责没有复制进 replacement，也没有在冻结 package 增加 import shim。架构守卫现禁止整个 `agents.screenplay` 与公开 Screenplay Agent schema 重新导入 `domains.screenplay_agent`；replacement、路由/schema、composition/lifespan、冻结联合回归通过。Electron 资源已重建，工作区、`build-resources` 与 `.app` 内现行合同逐字节一致；包内导入确认公开 schema 使用 `agents.screenplay.contracts`，应用签名、DMG、PurrA wheel 哈希和端口清理门禁通过。下一批迁移 `application.screenplay_v2_service` 的项目/Revision 现行业务边界；本项未删除旧 domain 或 legacy tests。 |
| 2026-09-14 | P4 Screenplay V2 Application Boundary Migration（完成） | 公开 Screenplay v2 路由实际使用的 17 个项目业务方法已整体迁入 `agents.screenplay.project_service`，覆盖项目 CRUD、会话、Working Copy、Revision 查询/接受、Review adjudication、定稿与 PDF 导出；旧 Agent profile、Planner 或 executor 没有被搬入。生产 `routers.screenplay_v2` 与现行路由测试直接导入新模块，PDF monkeypatch 也指向新所有者，没有在冻结 `application.screenplay_v2_service` 保留转发 shim。首次放入 `backend/application` 被冻结守卫明确拒绝后，按 replacement 目录规则纠正到 `backend/agents`；新旧 Screenplay、replacement、API/schema、composition/lifespan 与 194 文件冻结联合回归全部通过。Electron 资源已重建，工作区、`build-resources` 与 `.app` 内新 service/路由逐字节一致，应用签名、DMG、PurrA wheel 哈希与端口清理门禁通过。旧 service 当前只剩 frozen profile 与 legacy 专项测试调用，待 legacy runtime 同批删除。下一批迁移开发诊断使用的历史工具标签只读 adapter。 |
| 2026-09-14 | P4 Screenplay Historical Tool Presentation Adapter（完成） | 开发工具诊断已从冻结 `application.screenplay_tool_presentation` 切到 `agents.screenplay.historical_tool_presentation`。新 adapter 只读取冻结 Run 的 binding、Unit metadata 与 `tool.calls_started` 参数，为 13 种历史 read tool 重建 `displayName`；不修改 journal，不暴露参数到公共会话，不含 tool schema、handler、执行、候选写入或 runtime projector。必要的纯中文映射收敛在 `legacy_tool_labels.py`，已解除对 `domains.screenplay_agent.tools.catalog` 的依赖；13 类代表输入与旧映射逐项一致，并有独立期望值回归。冻结旧 module 当前只剩旧 SSE 与 legacy 测试引用。诊断、Screenplay 新旧链路、replacement、API、tool catalog、composition/lifespan、194 文件冻结和 diff/compile 门禁通过。Electron 资源已重建，工作区、`build-resources` 与 `.app` 内新 adapter、映射和诊断路由逐字节一致，应用签名、DMG 与 PurrA wheel 哈希通过。下一批进入 legacy runtime 删除批次前的最终调用图与历史读取边界核验。 |
| 2026-09-14 | P4 Screenplay Legacy Runtime Deletion Batch Preflight（完成） | 对 63 个冻结 Screenplay 路径重新生成 import 调用图，现行 router、composition、lifespan 与 `agents.screenplay` 均不可达旧执行链。冻结集合之外仅有三个旧 `sqlite_screenplay_*repository/finalizer` 导入冻结类型，而它们也只被冻结 runtime/projector 与 legacy 测试调用，因此首个源码删除批次应是 63 个冻结路径加这 3 个遗留 persistence 残片，不能拆开留下坏 import。删除边界不包含数据库：replacement 仍直接使用 `screenplay_agent_turns`、operations、项目/Revision、receipt 及通用 Run/journal/LongTask 表，`planner_run_id` 仍作为物理存储列并在 SQL 边界别名为 `root_run_id`；历史终态 Turn 继续由 replacement query 读取，legacy implementation identity 继续只读识别。legacy-only 测试删除、混合测试拆分和现行 fixture 保留清单已写入迁移文档。本项只完成预检，没有删除源码、schema 或历史数据；下一项执行首个封闭执行岛删除批次。 |
| 2026-09-14 | P4 Screenplay Legacy Runtime Deletion Batch 1（完成） | 已删除 63 个冻结 Screenplay 路径、三个旧 `sqlite_screenplay_*repository/finalizer` 以及 7 个 legacy-only 测试/支持路径，源码与测试合计 73 个路径；并拆分仍有通用价值的混合测试，没有增加 import shim、双写或 fallback。冻结机制同步移除 Screenplay 目录/前缀并从 194 个文件收敛为 131 个，仅继续冻结 Writing 与 Novel Analysis。现行数据库表、历史行、`planner_run_id` 物理列、legacy implementation identity、历史终态 Turn query 和历史工具标签只读 adapter 保留。静态生产 import 守卫、扩大 Screenplay/路由/恢复/历史回放回归、89 项 replacement acceptance、TypeScript、38 项 Electron 单测和 package 回归通过；重新生成的 `build-resources` 与本地 ad-hoc 签名 `.app` 均不含旧路径，应用深度签名与 DMG 校验通过。该包未公证、未发布。Screenplay 现只剩唯一 PurrA-native 执行源码；下一优先项回到 Novel Analysis 独立稳定观察 2。 |
| 2026-09-14 | R4 Writing 人物总数 Live Acceptance（完成） | 新增 Writing 专属的 fail-closed 隔离 Electron 启动器与标记目录校验，不再借用 Novel Analysis/Screenplay 开关；14 项 Electron 后端进程测试通过。重建并 ad-hoc 签名本地 macOS arm64 package，`codesign --verify --deep --strict` 与 DMG `hdiutil verify` 均通过。打包应用使用独立临时库创建三位虚构人物，真实 GLM-5.3-Flash Root `run_04aa3b47648047e2` 自主选择 `listBookCharacters(limit=1)`；持久工具结果明确为 `total=3`、`countScope=current_book_owned_characters`，证明答案来自宿主总数而非返回数组长度。Run 以 `writing/purra-native/v1` 终态 done，Electron 全局会话回放显示“查看人物目录”完成及“三个人物”的相同统计口径。W2 的背景读取/审批写入不重复执行，继续采用已完成的源码 Run `run_9a2e25d4bbbb4845` 与打包 Run `run_6f9cf347fea3413f` 证据。R4 尚余 W3 关联章节/大纲、语义记忆、技法自动选择、continuation source 与恢复漂移的真实 gate。验收材料位于 `/private/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-Hv1tDN/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R4 Writing W3 关联上下文 Live Gate（完成） | 扩展 Writing 隔离验收驱动，打包应用在全新临时库中创建一章与一份章节大纲，并分别写入合成暗号。真实 GLM-5.3-Flash Root `run_d61be3501aa24aab` 只调用 `readAssociatedWritingContext`；持久工具结果返回 `chapterCount=1`、`outlineCount=1`，Run binding 同时冻结唯一的 `associatedChapterIds` 与 `associatedOutlineIds`，最终回答准确复述章节暗号“蓝鲸钟”和大纲暗号“赤色潮汐”。Electron 展示“读取关联写作资料”完成及相同最终回答；完全退出打包应用并用同一 SQLite 数据库重新启动后，历史会话、工具标签和答案仍可回放。该证据只完成 W3 的关联章节/大纲切片，不外推为整个 W3 Live Gate；尚余语义记忆、技法自动选择、continuation source 与快照漂移/恢复。验收材料位于 `/private/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-PwOQEV/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R4 Writing W3 Semantic Memory Live Gate（完成） | 当前桌面配置没有可用的 `memory_embedding_config`，因此没有擅自选择或调用付费 embedding Provider；验收驱动为隔离数据库显式安装 4 维 localhost 确定性 embedding fixture，并要求重启后才加载组件。fixture 只用于把一条合成长记忆写入本地 PurrA memory store，调用 1 次；Writing 主模型仍使用真实 GLM-5.3-Flash。Root `run_988fb54af95e4382` 只调用 `readSelectedWritingContext`，持久结果为 `longTermMemory.available=true`、1 条记录，Run binding 冻结 memory id，`writingContextSnapshot.longTermMemory.refs` 冻结相同 id/version=1；最终回答准确复述“月蚀港的守门人只认银色航标”。Electron 显示“读取已选写作记忆”完成及相同答案；完全退出打包应用并以同一 SQLite 与 memory store 冷启动后，历史会话、工具标签和答案仍可回放。该证据证明显式选择/读取、版本快照和历史呈现，不证明真实 embedding Provider 的兼容性或漂移失败关闭；尚余技法自动选择、continuation source 与快照漂移/恢复。验收材料位于 `/private/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-GoRhf4/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R4 Writing W3 Technique Auto-selection Live Gate（完成） | 打包应用在全新隔离库创建、seal、publish 并授权两条合成技法：“对白节拍”和“潮汐意象”；随后为全局会话 reserve `mode=auto` 的不可变 request input。真实 GLM-5.3-Flash Root `run_5e00a1d83b5147ff` 严格先调用 `listWritingTechniqueCandidates`，再依据候选元数据选中“潮汐意象”，使用其完整 `kind/id/versionId` 调用 `readWritingTechniqueContext`，最终从入口正文准确复述暗号“琥珀雨”。验收同时核对持久候选 snapshot 恰有两项，Run binding 中的 `inputId/requestDigest/snapshotDigest` 与 `writing_technique_request_inputs` 一致，首次 `automaticRefs` 与读取条目的 source ref 一致；这里的具体自动选择通过持久工具参数/结果证明，而不是错误声称 binding 直接保存最终选择。Live 回放额外暴露 usage 接口仍只识别 legacy `writingTechniqueSnapshot`、对 replacement 恒返回空数组；现已新增基于 frozen request input 与 `readWritingTechniqueContext` 持久结果的 fail-closed replacement 投影，不读取或回显技法正文。重建并验证本地 package 后，同一历史 Run 的 usage 返回“潮汐意象 / 自动选用 / SKILL.md”，Electron 既显示两条工具完成记录，也显示“本轮已读取的写作技法与方案 · 1”；完整冷启动回放仍通过。package 内关键文件与工作区一致，`codesign --verify --deep --strict` 和 DMG `hdiutil verify` 通过。该切片不证明中断恢复后的 ExecutionState 选择复原或候选漂移失败关闭；尚余 continuation source 与快照漂移/恢复。验收材料位于 `/private/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-m4hS55/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R4 Writing W3 Continuation Source Live Gate（完成） | 扩展 Writing 隔离验收驱动，通过宿主来源导入和 continuation 创建接口生成三章合成来源，并在第二章“潮门之前”末冻结分叉；第三章仅用于证明边界，暗号不得进入模型工具结果或最终回答。真实 GLM-5.3-Flash Root `run_f02bdee861cd444a` 严格先调用 `listContinuationSourceSections`，目录只返回分叉前 2 章，再使用目录返回的精确 `sectionId=yPfXCotI` 调用 `readContinuationSourceSection`，最终准确复述“白潮刻痕”，没有出现分叉后暗号“黑曜余烬”。验收同时核对 Run binding 的 `writingContextSnapshot.continuation`：`creationMode=continuation`，完整 binding 的 source revision、analysis、fork ordinal=1、canon snapshot 及两项 digest 与持久表一致；`continuation_source_sections` 也恰有两项。Electron 展示“查看续写来源目录 / 读取续写来源章节”两个完成步骤及相同答案；完全退出打包应用并使用同一 SQLite 和 Electron userData 冷启动后，历史会话、工具步骤和答案仍可回放。本项没有改动 replacement 运行时，只固化真实 gate；第一次 fixture 因正文以“第一章”开头被解析器识别为额外章节，在 Provider 调用前即失败，修正合成文本后通过，不能计作 Agent 失败。验收数据库位于 `/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-LON5wR/database/purrtypos.db`；未读取或修改真实作品。W3 剩余独立门是 Run 恢复时的快照漂移失败关闭。 |
| 2026-09-14 | R4 Writing W3 Snapshot Drift Live Gate（完成） | 复核后修正了原任务前提：Writing 普通聊天没有 Durable Task/Unit resume 或 checkpoint continuation 入口，孤儿处理只做终态结算；因此不能用新建第二个 Run 冒充“恢复”，也不为本 gate 额外发明恢复协议。验收改为在同一个真实 GLM-5.3-Flash Root 的两轮工具调用之间注入 continuation binding digest 漂移：`run_dbf84576d1aa4e68` 首先成功调用 `listContinuationSourceSections` 并取得冻结的 2 章目录，随后验收线程只修改隔离库的当前 binding digest；模型使用目录返回的准确 `sectionId=tsjMhKOw` 调用 `readContinuationSourceSection` 时，工具从持久 Run binding 取原快照并返回 `continuation_context_unavailable / continuation_context_snapshot_changed`，Root 正确以 failed 终态结束。持久事件和验收断言确认 frozen/current digest 不同，来源正文暗号“白潮刻痕”和分叉后暗号“黑曜余烬”均未进入最终响应。Electron 如实展示“执行失败”，目录步骤完成、章节读取步骤失败；完全退出并用同一 SQLite/userData 冷启动后，失败状态和两个步骤仍可回放。语义记忆、技法 input 与 continuation binding 三类漂移已有确定性 fail-closed 覆盖，本次只选 continuation 做代表性真实纵切，避免重复消耗 Provider。验收数据库位于 `/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-writing-acceptance-yqbC5P/database/purrtypos.db`；未读取或修改真实作品。至此 Writing W3 的现有产品契约 gate 完成；若未来要求可恢复普通 Writing 对话，需要另立 Durable workflow 设计任务，不能算作本轮缺陷修复。 |
| 2026-09-14 | R3 Novel Analysis 完整分析 Live Gate（完成） | 新增可复用的隔离验收驱动和 packaged Electron 启动模式。合成单节来源经真实 GLM-5.3-Flash 完成 7/7 recipe；恢复后的 Root `run_d00044911ee147a4` 生成待审核 Artifact `analysis_attempt_94654921ac7532bbb0e6c3c1700db362`，包含独立 story overview、1 条概览证据、8 条创作资料和 1 条写作技法，Provider lease 为 0。真实失败轮次暴露并修复了 Planner 对合法 `write/model` 的误拒、提交 schema 过宽、结果合同错误示例、模型修正轮次不足，以及 continuation Run 未继承原对话 session 的投影缺陷；业务 Run/Artifact 仍保持 attempt 级身份。最终 package 中同一对话正确显示“已完成 / 待审核”，可查看人物、写作技法、历史概览及原文证据；同时修正新 `analysisTechniqueResult` 已生成却被标题显示为“未生成写作技法”的前端摘要。该切片证明完整分析、失败后恢复、引用回查和历史呈现，不外推为整个 R3 完成；审核发布、运行中取消/恢复、编辑替换与生产切流仍待独立验收。隔离数据库位于 `/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-novel-analysis-acceptance-lgvYIB/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R3 Novel Analysis Review Publication Gate（完成） | 最终 package 在全新隔离库中由真实 GLM-5.3-Flash 生成 Root `run_9481c764d0bf4b39`，7/7 单元一次完成并形成 pending Artifact `analysis_attempt_59e60b1272e7987c92fd02a7a7501667`；14 次 SDK 请求、0 Provider lease。新增发布验收驱动，经正式 HTTP 合同追加一条人工审核文本，创建 immutable reviewed Artifact `analysis_review_e9fb57ab5faa2a4d5afe928f25f1f476`，发布 `analysis_e71f8c23af3c4ff5bd1b25627cff4600`。发布重放保持同一 analysis id；原 attempt Artifact 未变化；正式表含 9 条事实、1 条 craft card、10 条证据，Run 投影切换到 reviewed Artifact 并带 `publishedAnalysisId`。完全退出最终 package 后以同一 SQLite 冷启动，API、正式资料与 Run 历史复核一致。Electron 冷启动后，来源库显示“已保存分析”，原对话显示“分析结果 已保存”，结果面板显示“保存修改”并回放人工审核正文；资料的来源依据可展开，能进入证据视图并定位到唯一原文章节。隔离数据库位于 `/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-novel-analysis-acceptance-28ocyn/database/purrtypos.db`；未读取或修改真实作品。 |
| 2026-09-14 | R3 Novel Analysis In-flight Control Gate（完成） | 新增三阶段真实验收驱动 `verify-novel-analysis-control-live.py`，在隔离库和打包应用后端分别证明暂停与取消不是同一状态。暂停样本 Root `run_245e5770dd464789` 以 canceled 结束、原 Task `longtask_6c7454b576e74cf5ab2db55dbd49573b` 保持 paused/resumable，并保留已完成 extract Unit；恢复创建 continuation Root `run_ec3331a2dee64fb1`，仍绑定原 Task，7/7 完成，暂停前 Unit 的 attempt/outputRef/artifactDigest/runId 四项身份未变化，Run binding 恰为 `created + continuation`。独立取消样本 `run_5c6311afdda849dc` / `longtask_fbff3c7d3a0b4639bfc36b87e47be175` 进入 canceled/non-resumable，resume 返回 409；三阶段结算后 Provider lease 均为 0。Electron 首轮检查发现页面顶部与任务卡显示“暂停”，但执行面板错误使用 canceled Root 显示“已取消”；现已让展示层仅在 durable task status 为 paused 时优先显示“已暂停”，不改写底层 canonical Root 记录。重建最终 package 后，第三个真实暂停点 `run_236ef43d9d5247d7` 的 Electron 冷启动回放同时显示“分析已暂停”“已暂停 · 55秒”“继续执行”“结束本次分析”，且没有“停止生成”。30 项 timeline、28 项 Analysis 会话、TypeScript、27 项 Analysis entry/recovery/executor 和冻结检查通过；隔离数据库位于 `/var/folders/c5/y2y2mkwd5n5ccrlt13qwjxhr0000gn/T/purrtypos-novel-analysis-acceptance-EUhV9a/database/purrtypos.db`。 |
| 2026-09-14 | R3 Novel Analysis Message Edit Replacement Gate（完成） | 扩展真实分析驱动以从正式 `analysis-follow-ups` 合同提交 `replaceRunId`。首轮最终包 Run `run_7f00699c4dc04702` 7/7 完成并生成待审核 Artifact，但暴露真实接线缺陷：初始 Root 被归档，随后同 Task 的 continuation Root 因 resume command 没有直接 session 绑定而留在旧分支。现已让编辑生命周期与 Run 投影采用相同会话规则：优先直接 command 绑定，否则沿 LongTask 的 `created` Root 继承 session，并把确定性测试改为无直接 session 的 continuation fixture。重建最终 package 后编辑 continuation `run_ec3331a2dee64fb1`，新 Root `run_e508e0a5edb14604` 真实 GLM-5.3-Flash 15 次 SDK 请求完成 7/7，生成 pending Artifact `analysis_attempt_5e4220763c6bb1d610d71d95ab875a60`；superseded 集合准确包含 continuation 与其后的旧 replacement `run_7f00699c4dc04702`，公开 Run 集合只剩新 Root，Provider lease 为 0。最终 Electron 冷启动回放仅显示 replacement 用户消息“重新分析这篇合成短篇……”、一条“已完成”状态和一个“待审核”结果；打开结果后读取的正是 `analysis_attempt_5e4220763c6bb1d610d71d95ab875a60`，界面显示 7 条创作资料、1 个写作技法及新结果中的沈岚/顾舟资料，旧暂停消息、continuation 与首轮 replacement 均未出现。替换/编辑定向测试 23 项、194 文件冻结校验、最终应用签名和 DMG 校验均通过，验收进程退出后 5174/18321/18322 端口无监听。 |
| 2026-09-14 | R3 Novel Analysis Production Cutover Gate（完成） | 普通 lifespan rollout policy 现默认选择 Writing、Novel Analysis、Screenplay 三个 replacement；Novel Analysis acceptance flag 只校验显式隔离目录与 marker，不再参与实现选择。最终包重建后，直接启动包内后端，使用全新普通数据库 `/private/tmp/purrtypos-novel-analysis-production-cutover-lioerz`，且未设置 `PURRTYPOS_NOVEL_ANALYSIS_REPLACEMENT_ACCEPTANCE`。正式来源导入、对话与分析 HTTP 入口创建 Root `run_4eeddc9e7f1642a5`，真实 GLM-5.3-Flash 14 次 SDK 请求一次完成 7/7，生成 pending Artifact `analysis_attempt_0c44393402be4eb2db312a5142a8b21e`，Provider lease 为 0。数据库仅有该一条 Run，其 `agent_kind/implementation_id/implementation_version/tool_contract_version/artifact_schema_version/recipe_version` 精确为 `novel_analysis/purra-native/1/1/1/1`，不存在新建 legacy identity。历史 legacy Run 仍只按已持久 identity 进入冻结读取/恢复路径，不提供新建回滚或缺失时回落。 |
| 2026-09-14 | R6 Novel Analysis Legacy Retirement Preflight（观察 1） | 在所有验收进程停止后，以 SQLite `mode=ro&immutable=1` 只读盘点真实默认持久库，未复制、迁移或修改数据库。按 implementation 字段、binding namespace 和 mode 联合识别到 425 条历史 Novel Analysis legacy Run：334 done、38 failed、53 canceled；running/paused/blocked 均为 0。关联 Durable Task 为 1 completed、10 failed、12 canceled，同样不存在 pending/running/paused。该结果只证明观察时点没有活跃 legacy 工作，不等于稳定窗口已经完成；frozen legacy 仍保留用于历史读取与持久 identity 路由，当前不删除。 |
| 2026-09-14 | R6 Novel Analysis Legacy Retirement Preflight（观察 2） | 18:17 CST 再次以 SQLite `mode=ro&immutable=1` 只读盘点真实默认库，425 个 legacy Run 和 23 个关联 Task 的终态分布与观察 1 完全一致，仍无 running/pending/paused/blocked。调用图审计同时否定了“可像 Screenplay 一样整包删除”的前提：冻结 service/session/source/artifact/tool 模块仍被 router、composition、lifespan、历史投影、公开 facts 与 Writing 技法生成调用。真实库还有 1,111 个 finalized legacy Artifact、9 个会话和 95 个 legacy session command，尚无 replacement Run/Artifact，已发布分析为 0。结论是可以退役 legacy Profile/Executor/Recovery，但必须先迁移现役会话和历史只读 adapter，并显式关闭 legacy 追问、编辑、恢复、审核、发布等变更行为。完整边界见 `docs/migrations/2026-09-14-novel-analysis-legacy-retirement-boundary.md`。盘点未读取 prompt、正文或 Artifact payload。 |
| 2026-09-14 | R6 Novel Analysis Session Boundary Migration | 新增 replacement-owned `agents.novel_analysis.sessions.NovelAnalysisSessions`，保持现有物理表、默认 `legacy:{revisionId}` 会话、command 幂等绑定、关闭/重开和公开 DTO 不变；正式 Novel Sources router 的新分析、追问和会话 CRUD 已不再导入冻结 session 模块。冻结 `application.novel_analysis_sessions` 仅剩 legacy service 的 replace-turn 内部调用，待纯 legacy 执行批次删除。新增架构守卫，35 项 session/边界测试、compileall 和 131 文件冻结校验通过；未访问或修改真实数据库。 |
| 2026-09-14 | R6 Novel Analysis Historical Read Adapter | 新增只读 `NovelAnalysisLegacyReadAdapter`，只公开历史 Run/Unit DTO 和 finalized Artifact 读取；正式 Run 列表、versioned SSE 与 legacy Artifact GET 已不再调用冻结 `NovelAnalysisService`。adapter 按旧 binding namespace 加持久 implementation identity 过滤，不导入冻结 service/profile/executor，不具备模型、Task control、Artifact mutation 或 publication 表面。新增同一 fixture 新旧 Run/Artifact DTO 完全等价测试与无变更能力守卫；151 项 Novel Analysis/会话/事件/entry 回归、compileall、diff check 和 131 文件冻结校验通过。真实数据库只沿观察 2 的 SQLite 只读元数据证据，本项未访问或修改真实内容。 |
| 2026-09-14 | R6 Novel Analysis Product Control Single Path | `VersionedNovelAnalysisProductService` 已删除 legacy service 注入与 start/follow-up/edit/pause/resume/cancel 回退；legacy identity 的 Task、Run 和 Artifact 变更请求在任何业务写入前统一返回“旧版仅供查看”。router 也不再导入冻结 `NovelAnalysisService`：legacy Artifact 仅由历史 adapter GET，review/publish 明确拒绝；正式资料列表/详情迁入只读 `NovelAnalysisPublishedQuery`。为保留前端已有“无结果只问原文”合同，replacement follow-up 支持无 Artifact Reactive Run，此时只授权来源目录/片段工具、不注入结果工具或 owner Run；真实 Core fixture 已完成来源工具调用与最终回答。211 项 backend 扩大回归、Novel Sources composer 行为测试、compileall 和架构守卫通过；未修改真实数据库或冻结源码。 |
| 2026-09-14 | R6 Novel Analysis Legacy Runtime Detach/Delete Preflight（完成） | AST 生产调用闭包否定了“下一步可直接断开并整包删除”的前提。会话、历史 adapter 和产品控制已经迁出，但 lifespan 的旧 recovery 仍承担 due-task monitor，而且只按统一 namespace 扫描、未隔离 replacement kind/identity；现有 replacement recovery 只有人工 continuation，没有自动 coordinator。`novel_analysis_reliability_baseline` 则是仍有效的 namespace 级可观测性，必须迁移而非删除。Writing 技法生成还动态导入旧 executor 的 candidate helper/model scope，并依赖旧 tool schema、SourceReader、ArtifactStore，因此 executor/tools/source/artifacts 属于跨 Agent 共享岛，不在 Novel Analysis 首批删除集合。修订顺序为 replacement 启动职责接管、composition/lifespan 断线、纯 legacy 源码与测试删除、Writing 共享抽取；完整清单见 `docs/migrations/2026-09-14-novel-analysis-legacy-retirement-boundary.md`。本项仅更新可执行边界，未改冻结源码、生产接线或真实数据库。 |
| 2026-09-14 | R6 Novel Analysis Replacement Startup Ownership（完成） | 新增 replacement-owned automatic recovery coordinator：due-task SQL 同时过滤 replacement namespace、task kind 与 owner Run 的完整持久 implementation identity，legacy 与不完整 identity 不会进入 runtime resolution；串行扫描加 task 级 in-flight 集合阻止同进程重复调度，continuation 仍执行最终 owner/status 校验。恢复 lifecycle 新增显式 source，自动恢复持久为 `automatic`，人工恢复默认仍为 `user`。namespace 级 reliability baseline 等价迁入 `agents.novel_analysis`，保持表、6 小时 bucket、100-task window 与 metrics schema。lifespan 已完全停止导入冻结 recovery/baseline，启动扫描、monitor 与 shutdown 均接入新 owner；94 项 replacement/恢复/lifespan/架构测试、compileall、131 文件冻结校验和 diff check 通过，未访问真实数据库。 |
| 2026-09-14 | R6 Novel Analysis Composition Runtime Detach（完成） | 生产 `composition_factory.py` 已停止导入和安装冻结 Novel Analysis Profile；versioned profile registry 的 Analysis namespace default 固定为 `novel_analysis.purra-native.v1`。即使调用者传入遗漏 Analysis 的部分 rollout policy，versioned composition 也强制保持 replacement create identity，不再保留“配置关闭后路由到旧 Profile”的半失效控制面。legacy implementation mapping 继续作为历史 identity tombstone，支持历史 Run 查询和只读拒绝，但生产 Profile registry 不含可执行 `novel_analysis`。冻结专项测试迁入 `tests/support/legacy_novel_analysis_composition.py` 显式构造，不进入产品接线；历史 adapter、SSE、产品控制、composition/lifespan 与 legacy fixture 回归通过，冻结源码未改。 |
| 2026-09-14 | R6 Novel Analysis Pure Legacy Source/Test Deletion（完成） | 已删除旧 Profile、Service、Runtime、Recovery、Reliability 原文件、Session 原文件、Stream、PublicFacts，以及 legacy-only 测试夹具和两个仍启动旧服务的过期验收脚本；混合测试已移除旧 planner/dispatch/follow-up/retry/review/publish 行为。历史 Run/Artifact、会话、正式资料与 SSE 分别继续由 replacement-owned session、published query、legacy read adapter 和 versioned projection 提供，legacy mutation 仍失败关闭。SSE 进一步固定只有具备 Durable Task membership 的 legacy Unit 可进入历史，孤立 Unit 不公开。冻结清单由 131 收敛为 124；`novel_analysis_progress.py` 因保留工具目录仍动态调用而延期到共享岛迁移。扩大后的 Novel Analysis、history、SSE、Writing shared-contract、composition/lifespan 回归通过；未访问真实数据库。下一项为 Writing Shared Analysis Utility Extraction。 |
| 2026-09-14 | R6 Novel Analysis Shared-Island Reachability Correction 与退役（完成） | AST 反向 import 图否定了“Writing 生产运行时仍依赖旧分析共享岛”的前提：`writing_technique_generation_tools` 只被已退役的旧 Analysis tools/executor 闭环调用，Writing legacy/replacement/profile/router/service 均不可达。没有为了完成计划而复制死代码；只把 continuation 使用的 canonical JSON digest 提取到中立 application 模块，把历史 reader/router 所需的旧 namespace/ref prefix 收敛为无执行能力的 `legacy_contracts`，随后删除旧 executor/tools/source/artifacts/progress、技法生成 tools、五个 analysis helper、旧 domain recipe/prompt、legacy-only 测试和过期模型验收脚本。历史 fixture 已改为直接持久化最小旧 Artifact，架构守卫禁止退役模块回流。全量回归另修正已失真的“每个产品都使用 Core Planner”测试：当前 Novel Analysis 使用 Core model planner，Writing 是 reactive，Screenplay 使用 host recipe planner。冻结清单由 124 收敛为 118，只剩 Writing legacy 范围；2,019 项 backend 测试、完整收集、compileall、diff check 和冻结校验通过。本批同时完成原“Writing Shared Analysis Utility Extraction”（无需迁移）与“Novel Analysis Shared-Island Retirement”；下一项为 Writing Legacy Runtime Detach/Delete Preflight。 |
| 2026-09-14 | R6 Writing Legacy Runtime Detach/Delete Preflight（完成） | 调用图否定了“118 个冻结路径都可作为旧 Writing runtime 删除”的前提：其中 76 个 Python 模块在模拟断开旧 Profile 后仍有 35 个被现役产品入口触达，包含正式 Agent façade/请求收据/历史提案、写作技法、故事记忆、统一记忆和知识向量索引；它们应重新分类，不应复制或删除。真正不再可达的是 41 个 Python 模块，另有 42 个 `backend/skills` 文件只由旧 SkillCatalog 动态读取；两个包级 `__init__` 需保留中性包壳。真实默认库两次 SQLite immutable 只读点查均为 18 个 legacy Writing Run（13 done、4 failed、1 canceled）、0 active，replacement done 2；未读取作品、prompt 或结果正文。当前生产新建已选 replacement，但组合根仍安装旧 Profile，部分 policy 仍可重新开放旧 create。修订顺序为 replacement 请求/响应合同归属、composition 强制断线、纯旧 runtime/tool/skill 删除、冻结清单重分类；完整边界见 `docs/migrations/2026-09-14-writing-legacy-retirement-boundary.md`。 |
| 2026-09-14 | R6 Writing Replacement Product Contract Ownership（完成） | 新增 `agents.writing.request_contract/context_claims/response_contract/public_facts`，正式 `request_mapping` 不再导入旧 Writing context、contracts、response 或 public-facts orchestrator；新请求只持久化 replacement 所需 locator，不再填充旧 Profile hydration 空槽。HTTP wire、Run binding、context allocation、响应 constraint/validator 与公开 facts 行为由既有完整响应测试保持。审计同时发现 replacement Profile 在切流后遗漏了旧 Profile 的 atomic-continuity Judge，现由新 response contract 恢复。四个纯校验原语继续复用并明确重新分类，不复制约 1,600 行校验算法。模拟断开旧 Profile 后，不可达冻结 Python 模块由 41 增至 48，可达由 35 降至 28；下一项可以断开 composition runtime。 |

| 2026-09-14 | R6 Writing Composition Runtime Detach（完成） | 生产组合根已停止 import/安装冻结 `writing` Profile，三个 Agent kind 均被强制固定到 replacement create route，部分 rollout policy 不能重新开放任一 legacy runtime；legacy implementation registry 仅保留历史 identity tombstone。`main.py`、配置、Electron 与 Web launcher 已移除 `SKILLS_DIR`/`PURRTYPOS_SKILLS_DIR`。遗留 Planner/检索/工具专项测试暂由 test-only helper 显式构造冻结 Profile，不进入生产接线，并将在下一删除批次清除。完整 backend、Electron 14 项、compileall、diff check 和 118 文件冻结校验通过，三测试端口无监听；下一项为 Writing Pure Legacy Runtime/Tool/Skill Deletion。 |

| 2026-09-14 | R6 Writing Pure Legacy Runtime/Tool/Skill Deletion（完成） | 从 118 个 Writing freeze 路径中删除 45 个纯旧 Python 源文件和全部 42 个旧技能文件，包括旧 Profile、eager context、planner/policy、tool catalog/schema/handler/cache/retriever 及 legacy-only 测试；不可运行的旧技法验收脚本和只描述旧工具序列的诊断 evaluation/HTTP 端点同步移除。剩余 31 个 Python 文件属于现役 façade、请求收据、历史提案、技法生命周期、故事/统一记忆、知识索引、replacement 校验原语与两个中性包壳，明确退出 freeze，不复制也不误删。freeze baseline 暂为空，完整 backend、Electron 14 项、compileall、diff check 与删除扫描通过；下一项为 Writing Legacy Retirement Final Verification。 |
| 2026-09-14 | R6 Writing Legacy Retirement Final Verification（完成） | 修复 Writing Electron 验收标志未进入 Python fail-closed 目录校验的接线遗漏，并增加 marked/unmarked 回归。完整 backend、TypeScript、505 项串行前端单测、38 项 Electron 单测、历史 Run/会话/proposal 回放、Vite 与 Electron build 通过；开发 Electron 和最终 packaged app 均只用新临时库显示合成人物及背景。删除后的 208 个 backend 路径在 `build-resources` 与 `.app` Resources 中均为 0 命中，PurrA wheel hash、应用深度签名和 DMG 校验通过。空 freeze manifest、校验脚本与专项测试已经删除；生产只保留三个 PurrA-native 执行路径，历史 legacy identity/adapter 仅供只读查询。未读取或修改真实作品，未公证、发布、push 或 tag。下一项为 Three-Agent Retirement Documentation Closure。 |
| 2026-09-14 | Three-Agent Retirement Documentation Closure（完成） | 根 README、文档索引、运维手册与安全威胁模型已改为三个 replacement Agent 的唯一现行入口，删除 PurrA 0.5.0、旧 Writing 分层、`backend/skills` 和已移除诊断端点仍被描述为当前能力的错误。旧架构说明及四份 `LEGACY_CONTRACTS.md` 明确标记为只读历史快照；历史 F0 freeze 清单保留在迁移计划中仅作证据，并注明冻结机制已删除。公共对话与模型请求规范不再固定过期依赖版本，候选身份统一指向 requirements 与 manifest。该项只修改文档，不改运行时、数据库或产品行为。下一项仅为 Post-Retirement Scope Review。 |
| 2026-09-14 | Post-Retirement Scope Review（完成） | 逐项复核最终回归、真实 Provider/Electron 证据、候选依赖与未授权发布边界。三个 Agent 重构目标已经闭合，没有事实依据继续新增框架层、兼容层或 Agent 执行功能。剩余事项仅为独立发布/分发 gate、可选真实 embedding Provider 兼容验收，以及并发前端测试进程退出问题的单独工程调查；它们不阻塞当前源码重构完成，也不能未经授权自动启动。结论见 `docs/migrations/2026-09-14-three-agent-retirement-scope-review.md`。 |

## 15. 后续执行队列（2026-09-12 确认）

以下顺序是依赖顺序，不是按目录平均分配工作。任何阶段只有达到退出条件后，才允许开启对应 Agent 的新 Run 默认切流。

### R1：完成 Novel Analysis A3 纵切

1. 定义 extract、normalize、overview、distill、coverage、review 的 v1 输入/输出 schema 和宿主 validator；模型缺少必需提交、结构化输出无效、证据 handle 无效均归入可恢复的模型/工具输入错误，不能成为永久业务失败。
2. 实现 replacement Unit executor。每次模型调用都绑定 `(rootRunId, taskId, unitId, attempt)`，只加载该 Unit capability manifest 允许的工具，并把提交结果写入 attempt Artifact。
3. 实现 DurableTask descriptor、dispatcher 和 Unit winner：只有 PurrA 成功结算的 Unit `output_ref` 是胜者；旧 attempt Artifact保留诊断价值，但不能进入下游依赖。
4. 将业务提交与阶段播报拆开结算。Artifact 已 finalized 后，stage/report 失败只能记录 presentation diagnostic，不能把 Unit 从 completed 改回 failed。
5. 实现最终 review Artifact v1，强制包含独立 `storyOverview`、证据覆盖报告和 technique result；legacy Artifact 只通过显式只读 adapter 进入历史查看，不写回旧 schema。
6. 新增 replacement 请求编译器：从现有 HTTP 请求和来源 revision 生成 canonical `sourceRevisionId/commandId/segments+sectionDigest`。当前 legacy service 产生的旧 domain payload 不符合新 Profile，这是切流前硬阻塞项。

退出条件：模拟 Provider 完成完整 recipe；覆盖分片并行、缺少提交、invalid evidence、Provider 暂时失败、取消、append 后中断、重启恢复、最终 review；Analysis rollout 仍保持关闭。

### R2：收敛 Shared S3–S6

1. S3：把 operation identity、usage、source receipts、Artifact owner 和 stage output 统一为共享 provenance value object/query，不改变各产品 Artifact schema。
2. S4：建立统一错误分类和 conformance fixture，至少覆盖 transient provider、model output invalid、tool input invalid、business invariant、canceled、execution interrupted；重试决策同时读取 attempt、预算和 effect state。
3. S5：把 Novel Analysis 已验证的 capability manifest 模式抽到 shared validator，再接 Writing 和 Screenplay；检查 projected input、enabled schemas、prompt-required tools、side effects、final output 五者一致。
4. S6：统一 canonical Turn 查询、后台 task registry 和 SSE envelope/cursor；产品层只保留可见性与文案投影。断开 SSE 不取消 Run，启动失败只允许一层 settlement。

退出条件：Writing 与 Novel Analysis 共用 conformance fixture；并行/重试不覆盖 provenance；取消、断流、重启和事件重放测试通过。不要为了“共用”合并产品 recipe、Artifact 或审批规则。

### R3：Novel Analysis 验收与切流

1. 在隔离来源数据上运行确定性 Core/Provider fixture，然后运行真实 Provider 新 Run。
2. Electron 验证计划、阶段、取消/恢复、review、引用回查和历史重放。
3. 仅在上述通过后，把 `AgentKind.NOVEL_ANALYSIS` 加入 lifespan rollout policy；新 Run 走 replacement，已有 legacy Run 仍按持久 identity 回旧实现。
4. 重启应用后创建全新 Run 验证，不使用切流前已创建的 Run 证明接入成功。

退出条件：真实新 Run 形成可审核、可引用、包含 story overview 的 Artifact；重启恢复通过；普通新建入口只有 replacement，历史 legacy Run 仅按持久 identity 读取或恢复，不提供新建回滚或隐式回落。

### R4：补齐 Writing 验收缺口

1. 真实 Provider 验证“本书共有多少个人物”必须调用 `listBookCharacters` 并使用宿主 `total`，不能凭上下文计数。
2. 验证 W3 的关联章节/大纲、语义记忆、技法自动选择和 continuation source 在 Run 恢复后仍执行快照漂移检查。
3. Electron 验证这些 READ 工具的展示、错误文案和历史回放；写入能力不重复做已完成的背景代表性验收，只补未覆盖的高风险路径。

退出条件：W1/W3 达到真实验收；Writing replacement 已是默认新建路径且没有双写。

### R5：Screenplay P1–P4

1. P1：建立唯一 Part registry，统一 kind、recipe、输入 revision scope、工具权限、输出 schema 和展示元数据；删除 replacement 中 synthetic Part RunBinding 假设。
2. P2：实现 attempt-scoped Candidate Artifact；漏写候选是可重试 MODEL_OUTPUT_INVALID，写后中断先对账，不盲目重调模型。
3. P3：所有 Part 声明 admissible revision scope；历史 revision 可浏览不等于可作为候选证据，实际消费必须留下 receipt。
4. P4：checkpoint 从 operation receipts 投影；usage 先记 operation 再聚合 Root；保留原始模型异常，附属 finalize/report 错误只能进入诊断。
5. 安装 `screenplay.purra-native.v1` Profile 后完成隔离、真实 Provider、Electron、取消、恢复和历史回放验收，再单独开启 Screenplay rollout。

退出条件：每种 Part 覆盖成功、漏写、Provider 暂时失败、写后中断、重试、取消和恢复；新项目全剧本生成通过。

### R6：稳定观察与按 Agent 删除 legacy

1. 分别查询 Writing、Novel Analysis、Screenplay 的 running/paused/blocked legacy Run；不能只检查进程内 task registry。
2. 每个 Agent 独立经过稳定观察窗口后，先关闭 legacy 新建入口，再删除该 Agent 的旧 profile、executor、tool catalog、service 分支、projector 和对应测试。
3. 只保留历史查看、导出、取消所需的最小只读 legacy adapter；禁止保留双执行、双写或“找不到新实现就回落旧实现”的兼容分支。
4. 每次删除后执行全量 backend、frontend、Electron、历史 fixture、打包产物和冻结清单更新；最后才删除冻结机制本身。

退出条件：三个 Agent 均只存在唯一 replacement 新建路径；历史 legacy 数据仍可读；没有 active legacy Run；本地打包验收通过。发布、push、tag 仍需单独授权。

### 当前可执行任务

三 Agent 旧执行链退役已闭环，但 2026-09-15 重新打开了小说分析的可扩展架构改造。
容量元数据、Slice Compiler、Planner Contract、Map Child、Run-tree 适配与隔离 v2 Profile 已完成。
模拟 Provider Map 纵切也已在真实 AgentCore 中验证 Root/Child Run、`readNovelSourceSlice`、
Child 结果收取与 attempt Artifact 结算。Hierarchical Reduce 已完成第一批 Host 输入编译：
依赖 Artifact 的同 pass、fan-in、digest、无重叠 lineage 和输入预算均失败关闭，Child scope
不携带 findings 内容。Reduce 受限 Tool、独立 Child、归并 Artifact、冲突字段和同 attempt
重放已经完成确定性纵切。真实 AgentCore 的串行模拟 Provider 已跑通 4+ Map、fan-in=2 的
至少两层 Reduce，每层只消费直接依赖，最终 lineage 覆盖全部 Slice。并行阻塞已确认是多个
Unit 对同一 Root 并发 join 导致 waiting 状态竞争；现保留独立 spawn，并由 per-Root coordinator
把同一批 Child 合并为一次 join。默认 maxParallelism=4 的多层模拟 Provider 已约 2 秒完成。
Synthesize 已通过无参数受限 Tool 读取每个 pass 的最终根 Artifact，并在同一并行模拟
Provider DAG 中形成非空整书 `summaryMarkdown`、Planner 指定 sections 和完整 Slice lineage。
确定性 Coverage Gate 已从 Synthesis 反查各 pass 根 Artifact，重新核验全部 Slice lineage、
digest、唯一输入与非空总结，并形成 Root-owned coverage receipt。Review Child 现只通过无参数
受限 Tool 读取 Coverage 批准的 Synthesis，提交独立 Artifact；审核后的 `summaryMarkdown` 已作为
Root `finalResponse` 返回。

第一段恢复门禁已完成：进程中断或重启后，新 attempt 会优先收敛上一 attempt 已 finalized
的 Map/Reduce/Synthesize/Coverage/Review Artifact，跨 continuation Root 不重复调用模型；普通
模型失败不复用旧输出。join batch 被取消后 coordinator 仍可服务后续 batch。

第二段恢复门禁已完成：通用 orphan recovery 和无 live executor 的显式取消都会先持久化
取消 Root 的完整 Agent subtree，再结算 SQL Root，因此旧 Child 不会在 continuation 启动后
继续消耗 Provider。新鲜 Run-tree repository 重放确认 unfinished Root/Child 均为 canceled；
相关 startup/recovery/scalable 联合回归 163 项通过。

隔离真实 Provider 纵切已经完成：32K 窗口下 Host 将合成来源编译为 2 个 slice，Planner、
2 Map、Reduce、Synthesize、Coverage、Review 全部完成；持久事件确认每个 Child 只调用自身
获授权的绑定读取 Tool，Root 返回非空整书总结，Provider lease 归零。该证据不等于生产接入：
下一项先完成 scalable v2 的唯一生产新建入口和旧新建路径关闭，再以重启后的全新 Run 做
Electron 验收；256K/1M 与 500 万字合成门禁仍需补齐。
