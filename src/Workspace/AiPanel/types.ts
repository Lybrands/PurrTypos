/** 四层设定层级枚举（不含伏笔） */
export enum SparkIdeaLayerFour {
  Global = 0,    // 全局
  Outline = 1,   // 大纲
  Character = 2, // 人物
  Chapter = 3,   // 章节
}

/** 四层层级文案类型（API 入参等使用） */
export type SparkIdeaLayerFourLabel = '全局' | '大纲' | '人物' | '章节'

/** 枚举 → 展示文案 */
export const SPARK_IDEA_LAYER_LABELS: Record<SparkIdeaLayerFour, string> = {
  [SparkIdeaLayerFour.Global]: '全局',
  [SparkIdeaLayerFour.Outline]: '大纲',
  [SparkIdeaLayerFour.Character]: '人物',
  [SparkIdeaLayerFour.Chapter]: '章节',
}

/** 文案 → 枚举（API 返回为文案时用） */
export const SPARK_IDEA_LAYER_LABEL_TO_VALUE: Record<string, SparkIdeaLayerFour> = {
  全局: SparkIdeaLayerFour.Global,
  大纲: SparkIdeaLayerFour.Outline,
  人物: SparkIdeaLayerFour.Character,
  章节: SparkIdeaLayerFour.Chapter,
}

/** 四层层级枚举值，顺序固定 */
export const SPARK_IDEA_LAYER_FOUR_VALUES: SparkIdeaLayerFour[] = [
  SparkIdeaLayerFour.Global,
  SparkIdeaLayerFour.Outline,
  SparkIdeaLayerFour.Character,
  SparkIdeaLayerFour.Chapter,
]

/** 按顺序的层级文案数组（用于排序、展示顺序等） */
export const SPARK_IDEA_LAYER_ORDERED_LABELS: SparkIdeaLayerFourLabel[] = SPARK_IDEA_LAYER_FOUR_VALUES.map(
  (v) => SPARK_IDEA_LAYER_LABELS[v] as SparkIdeaLayerFourLabel
)

/** 伏笔类型 */
export const FORESHADOWING_TYPES = ['悬念', '道具', '线索', '对话'] as const
export type ForeshadowingType = (typeof FORESHADOWING_TYPES)[number]
