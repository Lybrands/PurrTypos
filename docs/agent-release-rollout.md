# Agent 发布门禁与灰度验证

## 目的

本机制把三件事分开：

1. 单次 Run 是否正确，由运行诊断判断。
2. 候选版本是否具备灰度资格，由发布门禁判断。
3. 灰度版本是否优于基线，由分组对比判断。

性能告警不会被伪装成运行失败；工具治理或运行正确性退化则会阻止发布或建议回滚。

## 标记版本和灰度组

启动后端前设置：

```text
PURRTYPOS_AGENT_RELEASE_VERSION=v0.6.0-canary
PURRTYPOS_AGENT_ROLLOUT_COHORT=candidate
```

每个新建 Agent Run 都会持久化这两个值。基线安装应使用例如
`v0.5.1` / `baseline`。开发环境不设置时分别使用 `development` / `local`。

## 上线前门禁

先确保 Runtime Regression 与 Security Red Team 全部通过，再完成五个固定
Pilot Case，并为每个 Run 提交人工评分，然后调用：

```http
POST /ai/agent-release/gate
Content-Type: application/json

{"pilotRunIds":["run_1","run_2","run_3","run_4","run_5"]}
```

决策含义：

- `approve_canary`：确定性回归、安全诊断、样本量和质量均达到门槛，可以进入小流量。
- `hold`：没有发现硬故障，但样本、评分或质量证据不足，继续收集证据。
- `reject`：运行回归、安全红队、安全诊断、工具治理、质量或 Run 身份存在硬失败，不允许灰度。

## 灰度比较

```http
POST /ai/agent-release/rollout
Content-Type: application/json

{
  "baselineRunIds":["run_base_1","run_base_2","run_base_3","run_base_4","run_base_5"],
  "candidateRunIds":["run_new_1","run_new_2","run_new_3","run_new_4","run_new_5"]
}
```

决策含义：

- `promote`：可以扩大候选版本范围。
- `hold`：样本或人工评分不足，或者 P95 延迟退化；暂不扩大，但不把正确 Run 判失败。
- `rollback`：工具治理、运行失败率或人工质量明显退化，应停止候选版本并恢复基线。

当前接口给出确定性的发布建议，不会自动修改安装包、环境变量或用户数据。真正执行发布和回滚仍由构建/部署流程完成，避免一个诊断接口自行改变生产状态。
