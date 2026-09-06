# 当前集单维度审阅

## 目标

依据绑定版本的草稿与场景计划，判断当前集在指定维度上的实际问题，并给出可定位、可执行的修改方向。

## 方法

1. 先用 getScreenplayEpisodeContext 读取当前集，将 draftRevisionId、sceneListRevisionId 分别对应绑定的草稿版本和场景计划版本。按需读取授权的其他集或项目文档核对连续性，保持审阅对象的版本不变。
2. 只评价指定维度。continuity 检查时空、物件、知情和状态承接；character_arc 检查动机、选择及变化依据；structure_rhythm 检查目标、阻力、转折和节奏；dialogue 检查对白的行动作用、人物区别与信息表达；format 检查场景标识和可表演、可拍摄的呈现。
3. 每个问题说明材料中的具体表现、造成的影响和需要改变的内容。sceneIds 只引用当前任务允许的场景；跨场问题列出直接相关场景，把同一原因造成的重复表现合并。
4. 按影响程度排列问题。critical 用于破坏核心逻辑或人物成立的问题，major 用于明显影响理解或推进的问题，minor 用于局部表达或格式问题。问题数量不超过允许场景数，也不为凑数添加问题。
5. 根据问题决定 verdict：没有问题时为 ready 且 issues 为空；局部修改可解决时为 revise；需要重构关键逻辑或连续段落时为 major_rework。缺少关键材料时说明缺失，不据此生成肯定的审阅结论。

## 完成标准

通过 writeScreenplayCandidatePart 提交当前 episodeNumber、reviewDimension 对应的候选；问题具有唯一 id、合法 severity、具体 description 和有依据的 sceneIds。结论与问题清单一致，正文不替代草稿，也不汇总其他维度的结论。

## 自检

- 所有判断是否针对已读取的绑定版本？
- 是否把个人偏好当成缺陷，或把计划内容误认为已经写成的事实？
- 修改方向是否能回应指出的原因，而非只要求“加强”“优化”？
- verdict、严重程度与问题实际影响是否一致？
