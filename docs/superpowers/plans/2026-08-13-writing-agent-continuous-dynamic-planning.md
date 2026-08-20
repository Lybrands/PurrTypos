# 小说 Agent 连续动态规划实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让小说 Agent 对所有有效 Agent 请求使用 LLM 语义规划，并在读取章节、正典、设定、伏笔和写作方法后动态调整未完成步骤，同时保持宿主范围、权限与候选稿边界。

**Architecture:** 复用 PurrA 已有 Planner、TaskSpec、tool loop 和 replanning，不新增小说专用规划器。Writing Profile 只提供规划资格、可信规划规则、轻量 host facts、按 TaskSpec 检索和工具权限。正式章节仍由现有 Writing 业务写入/候选协议控制。本计划依赖《Agent Root Run 与公开计划投影实施计划》完成。

**Tech Stack:** Python 3.12、PurrA dynamic planner、Writing Domain Adapter、SQLite Writing repositories、pytest、Provider Gateway。

## Global Constraints

- [ ] 先合并共同前置计划；不得在本计划重新引入 Writing 默认装配。
- [ ] 不修改 `packages/purra/**`，不创建 Writing 专用 Planner Run。
- [ ] 不用用户文本长度、固定关键词或硬编码计划步骤决定是否规划。
- [ ] 规划阶段不加载整章或整本正文；正文与业务资料按 TaskSpec 和工具结果获取。
- [ ] 已完成步骤是执行历史，重规划只能增加、删除、合并或重排未完成步骤。
- [ ] 未经现有确认/应用流程，不得覆盖正式章节。
- [ ] Prompt 不能放宽 Tool Policy；书籍、章节、写作方法版本和写入范围仍由宿主解析。

---

## Task 1: 移除文本长度规划门槛

**Files:**
- Modify: `backend/domains/writing/planning.py`
- Modify: `backend/tests/test_writing_planning_policy.py`

- [ ] 先把 `test_writing_policy_applies_the_planning_eligibility_gate` 拆成参数化契约，至少覆盖：空文本、不绑定 book、两字中文 Agent 请求、Agent 模式且 tools disabled、Ask 模式且 tools disabled、Ask 模式且 tools enabled。
- [ ] 明确预期：有 book 且文本非空时，`mode=agent` 始终规划；`mode=ask` 仅在 tools enabled 时规划；其他非 Agent 模式沿用 tools-enabled 资格。
- [ ] 运行测试并确认两字中文请求因当前 `len(text) >= 8` 失败：

```bash
.venv/bin/python -m pytest backend/tests/test_writing_planning_policy.py -q
```

- [ ] 将 `WritingPlanningPolicy.should_plan()` 收敛为业务资格判断，不读取 `capabilities.available_tool_names` 推断前端工具开关：

```python
if not text or not context.book_id:
    return False
mode = (request.mode or "").strip().lower()
if mode == "agent":
    return True
return bool(request.tools_enabled)
```

- [ ] 保留 `planning_constraints()` 的章节绑定与工具依赖收紧，不改成 Prompt 字符串匹配。
- [ ] 重新运行测试并确认通过。
- [ ] 提交：

```bash
git add backend/domains/writing/planning.py backend/tests/test_writing_planning_policy.py
git commit -m "fix(writing-agent): plan every eligible agent request"
```

## Task 2: 定义小说动态规划的可信 Prompt 契约

**Files:**
- Modify: `backend/domains/writing/prompts.py`
- Create: `backend/tests/test_writing_prompts.py`

- [ ] 先新增 `build_writing_agent_policy()` 的精确文本测试，断言包含六项语义且不包含具体用户内容：目标导向计划、按需读取、工具后修订未完成步骤、已完成步骤不可改写、候选稿不覆盖正式正文、公开 commentary 不暴露私有 reasoning。
- [ ] 在 `prompts.py` 实现纯函数 `build_writing_agent_policy()`；内容必须区分：
  - 硬约束：人物身份、世界规则、已发生事件、角色已知信息。
  - 软约束：风格、节奏、视角、对白和描写习惯。
  - 计划规则：描述要达成的语义结果，不预写固定“读取/生成/校验”流水线。
  - 观察规则：工具返回新证据后更新尚未完成步骤。
  - 停止规则：证据不足时澄清；完成用户目标后停止额外扩写。
  - 输出规则：只给可公开的简短进度说明，不输出 chain-of-thought。
- [ ] 复用现有 `frame_untrusted_writing_context()` 处理用户/正文内容，不把业务材料拼到该 trusted policy。
- [ ] 运行测试：

```bash
.venv/bin/python -m pytest backend/tests/test_writing_prompts.py -q
```

- [ ] 提交：

```bash
git add backend/domains/writing/prompts.py backend/tests/test_writing_prompts.py
git commit -m "feat(writing-agent): define dynamic planning policy"
```

## Task 3: 在规划阶段只注入轻量事实和行为契约

**Files:**
- Modify: `backend/domains/writing/context.py`
- Create: `backend/tests/test_writing_context.py`
- Create: `backend/tests/test_writing_planning_context.py`

- [ ] 新增常量 `WRITING_AGENT_POLICY_CONTEXT = "writing_agent_policy"`，并在 `build_planning_context()` 返回一个 trusted `ContextBlock`，其内容只来自 `build_writing_agent_policy()`。
- [ ] 保留 `hostPlanningFacts.currentChapter`、绑定 book/chapter、可省略 chapterId 等结构化事实；不得把 chapter body、outline body、memory body 或 writing method body放进 planning bundle。
- [ ] 显式证据 manifest 模式仍可携带“有哪些证据已绑定”，但正文只在 `build_task_context()` 或工具调用阶段加载。
- [ ] 新增测试：
  - planning bundle 包含动态规划 policy；
  - planning bundle 不包含数据库中的独特正文标记；
  - post-plan `build_task_context()` 使用 `TaskContextRequest` 的 goal/operation/required blocks 构造 recall query；
  - book/chapter/method 的 host binding 不可被用户文本覆盖。
- [ ] 运行定向测试：

```bash
.venv/bin/python -m pytest backend/tests/test_writing_context.py backend/tests/test_writing_planning_context.py -q
```

- [ ] 提交：

```bash
git add backend/domains/writing/context.py backend/tests/test_writing_context.py backend/tests/test_writing_planning_context.py
git commit -m "feat(writing-agent): inject lightweight planning context"
```

## Task 4: 验证工具结果驱动未完成步骤重规划

**Files:**
- Modify: `backend/tests/test_agent_composition.py`
- Modify: `backend/tests/test_writing_chat_request_routes.py`
- Inspect only: `packages/purra/src/purra/engine/orchestrator.py`

- [ ] 用现有 scripted/fake model gateway 增加集成用例：初始 LLM 计划含“检查当前章节”“提出氛围改写”；`getChapterContent` 返回一个会改变策略的事实；后续 planner 输出保留已完成步骤并把未完成步骤改为新的语义步骤。
- [ ] 断言同一 Root Run 中至少出现两次 `run.todos_updated`，第二版：
  - 保留已完成步骤 ID 和 done 状态；
  - 只修改未完成步骤；
  - 不出现固定 `create/publish` 或 Recipe unit 标题；
  - 最终由同一 Root Run 完成。
- [ ] 增加失败用例：模型试图删除/重写已完成步骤时，现有 PurrA plan validation 拒绝非法 revision；Writing 层不吞掉该错误，也不创建第二个 Run。
- [ ] 若这些测试暴露 PurrA 已有重规划契约缺陷，停止实现并记录最小可复用 Core 缺口；不要在 Writing adapter 复制 todo 状态机。
- [ ] 运行集成测试：

```bash
.venv/bin/python -m pytest backend/tests/test_agent_composition.py backend/tests/test_writing_chat_request_routes.py -q
```

- [ ] 提交：

```bash
git add backend/tests/test_agent_composition.py backend/tests/test_writing_chat_request_routes.py
git commit -m "test(writing-agent): cover evidence driven replanning"
```

## Task 5: 固定候选稿与正式章节的写入边界

**Files:**
- Modify: `backend/tests/test_writing_tool_runtime.py`
- Modify: `backend/tests/test_writing_chat_request_routes.py`
- Modify only if regression is proven: `backend/infrastructure/writing/tools/handlers/chapter_tools.py`

- [ ] 先用现有 `editChapterContent` 路径写回归测试：Agent 生成的正文变更形成当前产品协议中的候选/待应用结果，数据库正式章节内容在用户应用前保持不变。
- [ ] 断言工具参数的 bookId/chapterId 由 host binding 校验；模型传入其他书籍或章节 ID 时失败关闭。
- [ ] 断言绑定的 writing method revision 是精确版本，模型不能通过工具参数升级、解绑或改变优先级。
- [ ] 只有测试证明现有 handler 绕过候选流程时，才最小修改 `chapter_tools.py`：让 Agent 写工具返回 proposal/candidate receipt，正式写入继续由既有用户确认接口负责。
- [ ] 运行定向测试：

```bash
.venv/bin/python -m pytest backend/tests/test_writing_tool_runtime.py backend/tests/test_writing_chat_request_routes.py -q
```

- [ ] 若无需生产代码修改，仅提交回归测试；否则连同最小 handler 修复提交：

```bash
git add backend/tests/test_writing_tool_runtime.py backend/tests/test_writing_chat_request_routes.py backend/infrastructure/writing/tools/handlers/chapter_tools.py
git commit -m "fix(writing-agent): preserve candidate first edits"
```

## Task 6: 小说 Agent 回归与手工 Provider 验收

**Files:**
- Verify: existing frontend and backend suites.

- [ ] 项目所有者手工绑定一本测试书和一章短正文，请求“深化弄堂氛围”，观察 LLM 计划、章节读取、计划 revision、候选结果和 Root Run 终态。
- [ ] 手工验收：
  - 只有一个 Root Run；
  - 初始计划来自 LLM；
  - 工具结果后未完成步骤可变化；
  - 公开计划不含 LongTask/Recipe 固定步骤；
  - 正式章节未被自动覆盖；
  - 手动取消后无 running Root/Child Run。
- [ ] 运行定向与完整门禁：

```bash
.venv/bin/python -m pytest backend/tests/test_writing_planning_policy.py backend/tests/test_writing_prompts.py backend/tests/test_writing_context.py backend/tests/test_writing_planning_context.py backend/tests/test_writing_tool_runtime.py -q
npm run check:agent-refactor
git diff --check
```

- [ ] 真实 Provider 验收由项目所有者在仓库测试之外执行；不得用 fake gateway 结果宣称已完成真实验证。

## Acceptance Checklist

- [ ] 两字中文 Agent 请求也会触发 LLM 规划。
- [ ] 工具结果可以改变未完成步骤，已完成步骤保持历史真实性。
- [ ] 规划上下文没有整章/整本正文。
- [ ] 宿主绑定的书籍、章节、正典和写作方法版本不可被模型越权。
- [ ] 任务进度只显示 LLM 语义步骤。
- [ ] 候选稿不会在用户确认前覆盖正式章节。
- [ ] 没有新增 Planner Run、Writing 状态机或 PurrA Core 修改。
