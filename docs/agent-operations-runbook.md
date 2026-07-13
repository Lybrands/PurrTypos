# Agent 运行与故障处理手册

## 1. 开发完成后的最低验证

```powershell
py -m pytest backend\tests -q
npm exec tsc -- --noEmit
```

同时执行 Python 编译检查和 `git diff --check`。新增工具还必须确认 Tool Contract 与 Tool Policy 覆盖。

## 2. 用户报告问题时

首先索取 `run_id`，不要只根据聊天截图猜测。

1. 查询 `/ai/agent-runs/{run_id}/diagnostics`。
2. 确认 `release.version` 与 `release.cohort`。
3. 查看 Planner、context budget、model round、tool round 和 terminal Trace。
4. 将问题归为 Planner、上下文、模型兼容、工具权限、handler、审批或连接中断。
5. 修复后把该事故提升为 Runtime Regression 或 Security Red Team Case。

## 3. 常见诊断映射

| 现象 | 优先检查 | 禁止的快捷修复 |
|---|---|---|
| 不调用工具 | Planner outcome、当前步骤 allowlist、provider fallback | 开放全部工具 |
| 调错工具 | 当前步骤 Schema 与后端 allowlist | 只改提示词 |
| 一直 running | terminal Trace、断开处理、pending approval | 手工把数据库改成 done |
| 工具执行失败 | toolReliability、参数 JSON、对象归属 | 自动重试非幂等写入 |
| 回答失忆 | context budget、裁剪轮次、记忆召回 | 一股脑塞入全部历史 |
| 延迟升高 | Planner/model/tool 分项与模型轮数 | 直接删除核心上下文 |
| 跨书籍访问 | host bookId、目标对象 book_id | 相信模型传入的 bookId |

## 4. 发布流程

1. 为构建设置 `PURRTYPOS_AGENT_RELEASE_VERSION` 和 `PURRTYPOS_AGENT_ROLLOUT_COHORT`。
2. 确定性 Runtime Regression 必须全通过。
3. Security Red Team 必须全通过。
4. 执行五个稳定 Pilot Case，并提交人工评分。
5. 调用 `/ai/agent-release/gate`；只有 `approve_canary` 可以进入灰度。
6. 收集基线与候选 Run，调用 `/ai/agent-release/rollout`。
7. `promote` 才扩大范围；`hold` 保持现状；`rollback` 恢复基线版本。

## 5. 安全事件处理

出现以下任一情况，停止候选版本：

- 未授权工具实际开始执行。
- 高风险写入绕过审批。
- 审批 ID 可以重放。
- ID 型写操作影响其他书籍。
- 工具错误泄露密钥或本地敏感路径。

保留相关 Run、Trace 和版本信息。不要删除事故记录；先阻断，再复现，再将复现固化为永久测试。

## 6. 新增工具清单

1. 在 Skill Schema 中声明精确参数。
2. 注册 async handler。
3. 在 Tool Policy 中分类为 read、propose 或 confirm。
4. 为 ID 参数增加当前书籍/章节归属校验。
5. 限制参数和返回体大小。
6. 明确是否幂等、是否允许重试。
7. 为未授权、非法 JSON、审批拒绝和 handler 失败增加测试。
8. 启动时通过 Tool Contract 校验。
