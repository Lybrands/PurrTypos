# P1：观察引用输入导致长分析失败

## 事件与范围

run_87f774066e0d4b46 / normalize:merge:1:4 在完成 18 个单元后失败。readAnalysisObservations 收到的一个观察哈希末尾将 288ad 写成 28ad，同一错误编号出现两次；目录中已有正确编号。工具将其归类为 invalid_reference，导致整轮失败。

先前短编号改造只覆盖 evidenceId，没有覆盖观察目录 ID、mergedObservationIds 和技法 evidenceRefs。本轮补齐这条观察引用链路。

## 修复

- 观察目录输出 O 加作用域前缀及序号，读取结果使用 observationId；不再要求模型复制 contentDigest。
- 完整冻结输入生成同一映射，读取顺序与分页不改变编号。底层仍保留完整哈希。
- 最终归并的 mergedObservationIds、技法提交的 evidenceRefs 在校验与持久化前还原为真实哈希。
- 未知编号返回 tool_input_invalid，包含错误编号及重新查询目录的指引；不模糊匹配、不擅自替换。
- 重复读取去重；已取消任务和来源权限仍拒绝。历史完整引用可在当前冻结范围内精确解析。

## 验证及状态

129 项相关确定性测试通过，包含实际漏字符形式、查询目录后成功读取、技法使用短编号提交而持久化真实哈希、作用域隔离、归并还原、幂等与取消后拒绝。改动文件 diff 检查通过。

状态：代码修复与确定性回归完成；真实 Provider / 原任务恢复 / Electron 验收尚未进行，不将 P1 线上效果标为已验收。没有修改真实资料、历史 Run 或 PurrA 框架。后端需加载更新后才能使用新观察协议。
