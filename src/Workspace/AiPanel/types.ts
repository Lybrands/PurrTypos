/** 四层记忆层级枚举（不含伏笔） */
export enum MemoryLayerFour {
  Global = 0,    // 全局
  Outline = 1,   // 大纲
  Character = 2, // 人物
  Chapter = 3,   // 章节
}

/** 四层层级文案类型（API 入参等使用） */
export type MemoryLayerFourLabel = '全局' | '大纲' | '人物' | '章节'

/** 枚举 → 展示文案 */
export const MEMORY_LAYER_LABELS: Record<MemoryLayerFour, string> = {
  [MemoryLayerFour.Global]: '全局',
  [MemoryLayerFour.Outline]: '大纲',
  [MemoryLayerFour.Character]: '人物',
  [MemoryLayerFour.Chapter]: '章节',
}

/** 文案 → 枚举（API 返回为文案时用） */
export const MEMORY_LAYER_LABEL_TO_VALUE: Record<string, MemoryLayerFour> = {
  全局: MemoryLayerFour.Global,
  大纲: MemoryLayerFour.Outline,
  人物: MemoryLayerFour.Character,
  章节: MemoryLayerFour.Chapter,
}

/** 四层层级枚举值，顺序固定 */
export const MEMORY_LAYER_FOUR_VALUES: MemoryLayerFour[] = [
  MemoryLayerFour.Global,
  MemoryLayerFour.Outline,
  MemoryLayerFour.Character,
  MemoryLayerFour.Chapter,
]

/** 按顺序的层级文案数组（用于排序、展示顺序等） */
export const MEMORY_LAYER_ORDERED_LABELS: MemoryLayerFourLabel[] = MEMORY_LAYER_FOUR_VALUES.map(
  (v) => MEMORY_LAYER_LABELS[v] as MemoryLayerFourLabel
)

/** 伏笔类型 */
export const FORESHADOWING_TYPES = ['悬念', '道具', '线索', '对话'] as const
export type ForeshadowingType = (typeof FORESHADOWING_TYPES)[number]
