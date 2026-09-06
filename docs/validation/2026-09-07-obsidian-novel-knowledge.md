# Obsidian 小说资料首版验证与试用

日期：2026-09-07。实施范围为 PurrTypos 小说业务 A–D；Vault 只读，E 阶段未实施。PurrA 仓库及真实创作资料未修改。

## 1. 实施结果

| 阶段 | 已实现 | 仍需外部验收 |
| --- | --- | --- |
| A | 临时小说、独立 Vault、正式/待定/未来/知情范围/同名/Canvas 案例；可重复生成的隔离样本 | 真实 Provider 的无接入基线与对照结果 |
| B | Electron 选择目录、范围预览、只读绑定、Markdown/YAML/链接解析、修订快照、FTS5、刷新、解绑、重启和恢复授权 | 大型真实 Vault 的扫描耗时与各操作系统文件行为 |
| C | 自动上下文与两个小说检索工具、共同预算、持久 Run 权限、模型发送前组合校验、实际来源记录、历史预览、宿主生成的 Obsidian 打开动作 | Electron 目录选择和 Obsidian 实际定位；真实模型输入及生成质量 |
| D | 显式远程文本授权、独立 Qdrant、增量索引、精确/全文/向量融合、故障降级、修订筛选、索引调用与 Token 记录 | 真实 Embedding 服务可用性、检索与生成效果对照 |

代码实现不等于所有退出条件已验收。没有真实对照结果，不能宣称 Agent 准确性或速度提升。

## 2. 可重复验证入口

```sh
.venv/bin/python -m pytest backend/tests/test_novel_knowledge.py -q
.venv/bin/python -m pytest backend/tests -q
npm run typecheck
npm run test:unit
npm run check:purr-components
npm run build:web
git diff --check
```

`test_novel_knowledge.py` 使用 pytest 临时目录及临时 SQLite。模拟 Embedding 网关配合实际 Qdrant local 验证缓存；模拟生成 Provider 通过实际应用组合、聊天接口及持久 Run 验证输入和 `stream.opened.contextEvidence`，不向真实 Provider 发送内容。

覆盖包括：中文精确/别名/全文、检索准入在候选截断之前、状态/章节起止/知情范围、跨书与越界、符号链接、重复 ID、现有资料所有权、缺失/不可用/改名/坏 YAML、Canvas 排除、预算暂缓、篡改/过期凭据、解绑与重新绑定、无资料凭据时的范围撤销、数据库恢复、语义缓存丢失与故障降级、持久 Run 工具权限及输入凭据重放。

最终门禁结果见方案第 13 节。

浏览器尝试打开临时 API 端口 `18331` 时返回 `net::ERR_BLOCKED_BY_CLIENT`，因此没有将该轮浏览器操作记为页面验收。已分别验证临时后端 HTTP、应用 ASGI 流程和 Electron IPC 单元行为。临时服务与浏览器标签已关闭。

## 3. 桌面试用步骤

1. 使用更新后的代码重启 Electron 与其后端。打开一本测试小说，在工作区顶栏或记忆面板进入“创作资料库”。浏览器版不能授权持续读取本地目录。
2. 选择 **Vault 内本小说的独立子目录**，检查 Markdown 清单后点“确认只读连接”。不要选择包含其他作品的整个 Vault。该流程不会创建或修改笔记。
3. 在 Obsidian 中给要作为正式资料的笔记填写下面的元数据。无完整元数据的笔记可预览，但不会自动作为正式设定；与已有 PurrTypos 人物卡/设定重叠的笔记保留为参考，不转移原资料所有权。
4. 在“写作范围与资料规范”中选择正文、人物视角或作者讨论。Agent 使用当前写作章节；这里保存的章节用于检索预览及没有当前章节的任务。改变范围后发起新任务。
5. 先用“精确＋全文”搜索人名、别名或事实，查看召回、排除原因及原文版本。新任务中可要求 Agent 查询这份资料。实际发送记录在“模型调用实际来源记录”和开发诊断的来源凭据中查看。
6. 需要向量检索时，先在现有设置配置 Embedding，再在资料页明确授权向该服务发送文本，点击“索引下一批”。每批最多 40 个片段；初次大库需要多批。未授权时不会自动发起 Embedding 请求。本文没有验证任何真实收费服务。
7. 来源预览保留当次修订；“在 Obsidian 打开当前文件”打开磁盘当前版本。标题/块首版回退为打开文件并显示定位信息；关系图和 Canvas 在 Obsidian 内使用。

公开、全局有效的资料示例：

```markdown
---
purr_id: red-door
type: world
status: confirmed
temporal_scope: global
known_to: ["*"]
aliases: [朱门]
---
# 红门

红门只能用铜钥匙打开。
```

变化事实用 `temporal_scope: chapter_range`，`valid_from_chapter`/`valid_to_chapter` 填本作品稳定章节 ID，起章包含、止章不含；角色首次知情用 `known_to` 和 `knowledge_from_chapter`。不要用章节序号代替 ID。不同人物知情时间不同，拆成分别注明范围的笔记。作者讨论模式可读取合格的未来章节设定；正文及人物模式不能提前读取。

## 4. 临时样本与边界

```sh
.venv/bin/python scripts/create-novel-knowledge-fixture.py
```

脚本只创建一个新临时目录，输出隔离数据库、Vault、小说目录和测试作品 ID，不接受现有目标目录。它用于开发验证，不会向当前桌面数据库导入试点作品。开发者可把输出的 `dataDirectory` 配给独立后端进行 HTTP 验证；不要假设 Electron 会采用同名环境变量，当前 Electron 后端固定使用其应用数据目录。桌面试用时新建测试作品，并通过界面选择独立样本目录；样本内章节 ID 需对应测试作品。

首版不实现所有权转移、旧资料完整历史投影、资料建议写入、图谱嵌入、Canvas 检索或多机自动授权。绑定后正文/人物模式会暂时停用缺少历史与知情投影的旧记忆、人物/设定等自动来源及对应读取工具；作者讨论模式可显式使用当前状态。该取舍可能减少上下文量，不能据此宣称更准确。

扫描采用 5 秒清单轮询、检索前扫描、模型发送前来源复验，目录上限 2,000 篇、单篇 1 MiB、总扫描 64 MiB。持续编辑或章节内容变化会使旧凭据失效，需要新任务；扫描成本与更细粒度恢复体验留待真实试点测量。

SQLite 备份含已保存的来源修订，不含外部 Vault 文件。恢复数据库后需重新选择目录；解绑、删除测试作品均不删除 Vault 文件。
