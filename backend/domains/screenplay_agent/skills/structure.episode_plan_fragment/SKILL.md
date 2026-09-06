# 展开当前集结构

## 目标

在既定分集索引的边界内形成完整的单集故事，使目标、冲突、转折与集末状态互相支撑。

## 方法

1. 用 readScreenplayTaskDependencies 读取绑定的分集索引和相关主线阶段，确认当前集的 number、id、title 及概要边界。保持这些身份字段不变，不重新划分集数。
2. 根据本集开始时的人物目标和已有处境，确定需要完成的主要行动。objective 应具体可判断，conflict 应说明持续阻力以及它如何影响人物的策略或选择。
3. 在 summary 中写清起点、主要尝试、关键选择和结果，使事件通过行动与后果连接。turn 应由铺垫和故事条件产生，并改变解决问题的方式。
4. 从本集形成的结束状态设计 hook：它可以是未完成的行动、关系变化、信息揭示或新困境，应自然引出后续问题，不依赖无依据的突发事件。
5. 改编需要核对原作时，使用 inspectSourceStructure 返回的授权 chapterId 调用 readSourceChapters。按既定改编约束处理素材，不把原作目录当成新的分集安排。

## 完成标准

通过 writeScreenplayCandidatePart 提交 episode_plan 当前集候选。contentJson.episodes 只含绑定的这一集，保留 number、id、title，完整填写 summary、objective、conflict、turn、hook。正文与结构化内容一致，止于分集结构层次，不写场景表、完整场景或对白。

## 自检

- 主要事件是否确实完成索引规定的叙事任务？
- 关键转折是否源自本集行动，并带来可说明的新条件？
- 钩子是否与本集结果相连，而非另加一个无关事件？
- 是否重复消耗相邻集事件，或遗漏必要的承接条件？
