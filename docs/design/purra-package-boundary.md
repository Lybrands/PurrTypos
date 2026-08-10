# PurrA 独立包边界

## 定位

`packages/purra` 是可独立构建、仅依赖 Python 标准库的通用 Agent
框架。PurrTypos 是它的一个宿主，不是框架内部的特殊分支。

框架负责完整 Agent Run 的通用不变量：规划、上下文与输出预算、模型结束
原因、恢复额度、工具授权、Artifact、Work Item、检查点、取消、终态和标准事件。
产品负责领域上下文、业务任务编译、工具实现、持久化适配、Provider 适配和传输映射。

## 公开面

- `purra.api`：完整 Agent Run 的唯一执行入口；
- `purra.model_execution`：宿主模型型 Hook 的受管无工具调用入口；
- `purra.contracts`：宿主可以构造的稳定值对象；
- `purra.ports`：宿主与基础设施实现的端口；
- `purra.tools`、`artifacts`、`work_items`：需要宿主注册或实现的通用契约。

`purra.engine`、`runtime` 和模型轮次实现不是应用入口。产品代码不得自行
复制其中的预算、截断、修复或生命周期逻辑。

## 依赖方向

```text
Writing / Screenplay Domain
            ↓
PurrTypos Application Host
            ↓
purra.api + public contracts
            ↓
Agent Framework Runtime
            ↓ ports
Provider / SQLite / transport adapters
```

Adapter 在 Composition Root 注册给框架。领域和普通应用用例拿不到原始 Provider
Gateway，不能自行建立第二条 Agent 模型执行链路。

## 受管模型执行边界

迁移债务已经清零。Application、Domain 和 Router 不得直接构造
`ModelInvocation`，也不得在 Composition Root 之外取得原始
`ProviderModelGateway`。静态架构测试把这两条约束作为零容忍不变量，新增旁路会
直接使 CI 失败。

记忆重排、响应裁判、会话压缩和剧本结构化生成都只向
`ManagedModelExecutor` 提交 `ModelRequest`、`OutputBudgetPolicy`、工作单元数量和
推理模式。PurrA 负责解析唯一实际输出额度、构造供应商调用、记录调用参数、限制
兼容降级次数，并统一解释流式与非流式结束原因。

供应商没有给出结束原因，或者明确以 `length`、过滤或未知原因结束时，结果一律
失败关闭。尤其是已经输出部分正文后发生截断时，不得再次执行同一调用。业务层的
JSON 修复只允许处理“供应商已正常结束，但完整输出不符合业务 Schema”的结果，
不能修复或重放不完整输出。

剧本旧的 `screenplay_model_run.py` 已删除。替代它的
`screenplay_structured_call.py` 只拥有剧本提示、JSON 校验、Run/诊断持久化和可见
进度投影；它必须由唯一 Composition Root 注入受管执行器，无法自行创建 Provider
调用链。

## 打包

- 开发和测试从 `packages/purra/src` 加载；
- PyInstaller 将该 src root 加入分析路径；
- Electron 源码分发把 `purra` vendoring 到后端资源目录；
- 框架包本身不依赖 FastAPI、Pydantic、模型 SDK 或数据库驱动。

在第二个真实产品宿主接入并验证公共 API 稳定以前，框架保留在 Monorepo；届时再
决定是否拆为独立仓库，不提前引入跨仓版本协调成本。
