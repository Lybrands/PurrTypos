/** 四层设定层级枚举（不含伏笔） */
export enum SparkIdeaLayerFour {
  Global = 0,    // 全局
  Outline = 1,   // 大纲
  Character = 2, // 人物
  Chapter = 3,   // 章节
}

/** 枚举 → 展示文案 */
export const SPARK_IDEA_LAYER_LABELS: Record<SparkIdeaLayerFour, string> = {
  [SparkIdeaLayerFour.Global]: '全局',
  [SparkIdeaLayerFour.Outline]: '大纲',
  [SparkIdeaLayerFour.Character]: '人物',
  [SparkIdeaLayerFour.Chapter]: '章节',
}

/** 四层层级枚举值，顺序固定 */
export const SPARK_IDEA_LAYER_FOUR_VALUES: SparkIdeaLayerFour[] = [
  SparkIdeaLayerFour.Global,
  SparkIdeaLayerFour.Outline,
  SparkIdeaLayerFour.Character,
  SparkIdeaLayerFour.Chapter,
]

/** 伏笔类型 */
export const FORESHADOWING_TYPES = ['悬念', '道具', '线索', '对话'] as const
