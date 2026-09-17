# 服务商级模型预设（2026-09-17）

## 背景

内置目录原为六个模型级预设（`zai:glm-5.3-flash`、`deepseek:deepseek-v4-flash`、
`moonshot:kimi-k3`、`moonshot:kimi-k2.6`、`minimax:MiniMax-M3`、`mimo:mimo-v2.5-pro`），
模型名、能力上限与思考行为都随版本内置。服务商上新模型时必须发版才能接入。

## 变更

- 内置目录改为**五个服务商级预设**：`zai`、`deepseek`、`moonshot`、`minimax`、`mimo`。
  接入端点与协议由系统维护；**模型名称由用户在设置页填写**。
- 思考控制全部放开（`SELECTABLE`）：设置页以「模型支持思考」声明，按声明值构造
  enabled/disabled 请求；不再有任何 `ALWAYS_ENABLED` 硬约束。对话报思考配置错误时，
  错误呈现层追加引导回设置页调整声明。
- 能力上限默认 + 可覆盖：每家按主推模型登记默认上限；用户可显式填写
  `profileMaxGenerationTokens` 覆盖（`apply_user_declared_generic_capabilities`
  允许内置 profile 覆盖上限，仍拒绝覆盖思考能力）。
- 服务商级 profile 的 `matches()` 只校验接入端点，不校验模型名；
  `model_runtime` 的 zai 前缀校验接受 `zai` 与历史 `zai:` 前缀。
- **旧模型级 profile 冻结保留**（`LEGACY_MODEL_PROFILES`）：不再下发新配置，但
  `resolve_model_profile` 继续解析，服务存量配置与历史请求重放；pre-descriptor
  历史请求仍走 `legacy_profile_codecs.py` 冻结规则。
- 前端存量配置在读设置时一次性迁移：旧 presetId → 服务商 id，原内置模型名写入
  `name`，原登记上限写入显式覆盖，保证迁移后请求行为不变。
- `installModelDescriptors` 改为按 id 温和合并：后端下发行覆盖内置快照，缺失条目
  保留内置版本，前后端版本偏差不再中断设置加载。

## 契约

- 描述符生成链不变：`scripts/generate-model-descriptors.py` 从
  `BUILTIN_MODEL_PROFILES` 生成前端离线快照。
- 前后端一致性测试：`backend/tests/test_model_profiles.py`、
  `test_registered_model_contracts.py`、`src/modelCatalog.test.cjs`。
