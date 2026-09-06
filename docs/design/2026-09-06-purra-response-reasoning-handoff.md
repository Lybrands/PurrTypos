# PurrA 0.5.0 公开响应遗漏推理模式：框架交接

状态：已用已安装的 PyPI 0.5.0 离线复现；尚未修改或发布 PurrA。此问题阻断小说分析 Root 最终总结，不影响已经完成的分析 Artifact。

## 真实故障

临时验收任务 `longtask_e6b3c47cd09449d79df4b2965d2a2269` 的 11 个单元全部完成。Root `run_f51128d674224104` 在 `completion_projection` 失败，错误为 `model_reasoning_mode_conflict`。最终总结未发出 SDK 请求。任务 completed 与 Root failed 同时成立，不能用前者覆盖后者。

调用链：

1. PurrTypos `NovelAnalysisService._execute` 从公共 resolver 得到思考开启的请求，将 `reasoning_mode=ENABLED` 传入 `AgentCoreRunOptions`。
2. PurrA orchestrator 的 `_settle_runtime_result` 将同一值传入 `ModelInvocationContext.requested_reasoning_mode`。
3. `src/purra/output/response_transaction.py` 的 `_invoke_and_collect` 创建 `AgentModelCall` 时没有传 `reasoning_mode`，默认为 DEFAULT。
4. `src/purra/model_invocation/manager.py` 的 `_validate_call` 检查二者一致性并拒绝。这个检查是正确的不变量，不能删除。

已只读核对相邻源码 `/Users/liuyubin/Lybrand_project/purra/src/purra/output/response_transaction.py`，同样存在遗漏。没有改动该仓库、site-packages 或发行 wheel。

## 可复制复现

在 PurrTypos 中运行：

```sh
.venv/bin/python scripts/diagnose-purra-response-reasoning.py
```

该脚本调用实际 `AgentResponseTransaction`，在探针 invoker 中执行安装包自身的准入检查；不请求 Provider、不读配置、不访问数据库。存在冲突时以 1 退出，不能计作通过的回归测试。

0.5.0 实测：direct/default 和 presentation/default 通过准入；direct/enabled、direct/disabled、presentation/enabled、presentation/disabled 全部失败，SDK 调用 0。因此故障不限于 GLM，也不限于 analysis；凡进入该 response transaction 且 Run 推理模式不是 DEFAULT，都受影响。三个产品 Agent 并非所有输出路径都会进入这个类，不能据此断言所有对话必然失败。

## 框架修复范围与验收

目标项目：`/Users/liuyubin/Lybrand_project/purra`。最小修改位于 `AgentResponseTransaction._invoke_and_collect` 的 `AgentModelCall` 构造处：

```diff
 AgentModelCall(
     request=request,
+    reasoning_mode=context.requested_reasoning_mode,
     output_intent=intent,
```

应在框架内补齐 DEFAULT/ENABLED/DISABLED 三态的 direct-live、validated candidate、public presentation 契约测试；检查 TypeScript 对应实现是否有同类遗漏。验证真实 manager 接收同一推理模式、final_public/live、无工具权限，且预算、取消、流式提交/中止和单次调用身份仍受框架管理。

另需核对：Long Task 已 completed、Root presentation failed 后能否只恢复公开总结。当前 PurrTypos 的 task resume 只接受 failed/paused 的 Long Task，这个场景不能通过重复提交蒸馏任务解决。需使用或补齐框架公开恢复契约，再接入产品；不能手改历史 Run 状态或重跑已完成的 11 个单元。

禁止在 PurrTypos 的 Gateway 修改调用模式来掩盖不一致；Gateway 尚未收到本次调用。禁止将 Run 模式降为 DEFAULT、关闭 GLM 思考、复制框架 response transaction、monkey patch 安装包或将私有候选冒充公开总结。

修复可先在框架仓库完成测试，发布是独立确认步骤。发布后 PurrTypos 才能更新统一依赖清单，安装新包并重启，用相同配置验收 Root 最终真实流式总结。修复后再次运行上述脚本应报告 0/6 冲突。
