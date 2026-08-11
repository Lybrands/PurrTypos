import type { ScreenplayV2RevisionPart } from '../types'

const KEY_LABELS: Record<string, string> = {
  action: '动作',
  approach: '创作方式',
  beats: '情节点',
  characters: '人物',
  conflict: '冲突',
  description: '描述',
  dialogue: '对白',
  episodeCount: '集数',
  episodeNumber: '集数',
  evidence: '依据',
  fields: '创作设定',
  goal: '目标',
  issues: '问题',
  location: '地点',
  name: '名称',
  note: '说明',
  notes: '说明',
  outcome: '结果',
  objective: '目标',
  plot: '情节',
  premise: '核心设想',
  recommendation: '建议',
  risks: '改编风险',
  sceneText: '正文',
  scenes: '场景',
  severity: '严重程度',
  status: '状态',
  summary: '摘要',
  synopsis: '场景梗概',
  themes: '主题',
  time: '时间',
  title: '标题',
  turn: '转折',
  verdict: '结论',
  verificationResults: '复核结果',
  world: '世界观',
}

const HIDDEN_KEYS = new Set([
  'contentDigest',
  'executionSummary',
  'processSummary',
  'schemaVersion',
  'sourceRunId',
  'storageMode',
])

// These fields identify the document that the surrounding list/header has
// already selected. Repeating them inside the body adds noise without adding
// document content.
const TOP_LEVEL_CONTEXT_KEYS = new Set([
  'chapterIndex',
  'chapterNumber',
  'chapterTitle',
  'chapter_index',
  'chapter_number',
  'chapter_title',
  'documentKind',
  'document_kind',
  'episodeCount',
  'episode_count',
  'episodeIndex',
  'episodeNumber',
  'episodeTitle',
  'episode_index',
  'episode_number',
  'episode_title',
  'heading',
  'index',
  'itemCount',
  'item_count',
  'key',
  'kind',
  'name',
  'number',
  'partNumber',
  'part_number',
  'position',
  'revision',
  'revisionNo',
  'revision_no',
  'role',
  'sceneNumber',
  'sceneTitle',
  'scene_number',
  'scene_title',
  'slugline',
  'status',
  'title',
  'type',
  'version',
  'versionNumber',
  'version_number',
])

const ITEM_TITLE_KEYS = new Set([
  'chapterNumber',
  'chapterTitle',
  'chapter_number',
  'chapter_title',
  'episodeNumber',
  'episodeTitle',
  'episode_number',
  'episode_title',
  'heading',
  'index',
  'name',
  'number',
  'position',
  'sceneNumber',
  'sceneTitle',
  'scene_number',
  'scene_title',
  'slugline',
  'title',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function hasReadableValue(value: unknown): boolean {
  if (value == null || value === '') return false
  if (Array.isArray(value)) return value.some(hasReadableValue)
  if (isRecord(value)) return Object.entries(value).some(([, item]) => hasReadableValue(item))
  return true
}

function isTechnicalKey(key: string): boolean {
  return HIDDEN_KEYS.has(key)
    || key === 'id'
    || /(?:^|_)(?:id|ids)$/i.test(key)
    || /Ids?$/.test(key)
}

function escapeMarkdown(value: string): string {
  return value.replace(/([\\`*_[\]<>])/g, '\\$1')
}

function valueText(value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  return escapeMarkdown(String(value))
}

function keyLabel(key: string): string {
  if (KEY_LABELS[key]) return KEY_LABELS[key]
  const words = key
    .replace(/[_-]+/g, ' ')
    .replace(/([a-z\d])([A-Z])/g, '$1 $2')
    .trim()
  return words ? words[0].toUpperCase() + words.slice(1) : '内容'
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function itemTitle(value: Record<string, unknown>, index: number): string {
  const title = value.title
    || value.episodeTitle
    || value.episode_title
    || value.chapterTitle
    || value.chapter_title
    || value.sceneTitle
    || value.scene_title
    || value.name
    || value.heading
    || value.slugline
  if (typeof title === 'string' && title.trim()) return title.trim()
  const number = value.episodeNumber
    ?? value.episode_number
    ?? value.chapterNumber
    ?? value.chapter_number
    ?? value.sceneNumber
    ?? value.scene_number
    ?? value.number
    ?? value.index
  return number != null && String(number).trim()
    ? `第 ${String(number).trim()} 项`
    : `第 ${index + 1} 项`
}

function renderRecord(
  value: Record<string, unknown>,
  indent = 0,
  omittedKeys: ReadonlySet<string> = new Set(),
): string[] {
  const prefix = ' '.repeat(indent)
  const lines: string[] = []
  Object.entries(value).forEach(([key, item]) => {
    if (
      omittedKeys.has(key)
      || isTechnicalKey(key)
      || !hasReadableValue(item)
    ) return
    const label = escapeMarkdown(keyLabel(key))
    if (typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean') {
      lines.push(`${prefix}- **${label}：** ${valueText(item)}`)
      return
    }
    if (Array.isArray(item)) {
      const readableItems = item.filter(hasReadableValue)
      if (readableItems.length === 0) return
      if (readableItems.every((entry) => (
        typeof entry === 'string'
        || typeof entry === 'number'
        || typeof entry === 'boolean'
      ))) {
        lines.push(`${prefix}- **${label}**`)
        readableItems.forEach((entry) => {
          lines.push(`${prefix}  - ${valueText(entry as string | number | boolean)}`)
        })
        return
      }
      lines.push(`${prefix}- **${label}**`)
      readableItems.forEach((entry, index) => {
        if (!isRecord(entry)) return
        const title = itemTitle(entry, index)
        lines.push(`${prefix}  - **${escapeMarkdown(title)}**`)
        lines.push(...renderRecord(
          entry,
          indent + 4,
          ITEM_TITLE_KEYS,
        ))
      })
      return
    }
    if (isRecord(item)) {
      lines.push(`${prefix}- **${label}**`)
      lines.push(...renderRecord(item, indent + 2))
    }
  })
  return lines
}

export function structuredContentToMarkdown(
  value: Record<string, unknown> | null | undefined,
): string {
  if (!value) return '*暂无可阅读的正文内容*'
  const lines = renderRecord(value, 0, TOP_LEVEL_CONTEXT_KEYS)
  return lines.length > 0 ? lines.join('\n') : '*暂无可阅读的正文内容*'
}

export function revisionPartKey(part: ScreenplayV2RevisionPart): string {
  return `${part.type}:${part.key}:${part.position}`
}

export function revisionPartTitle(
  part: ScreenplayV2RevisionPart,
  index: number,
): string {
  if (part.type === 'document' && part.key === 'main') return '完整文档'
  const rawTitle = part.payload.title
    || part.payload.episodeTitle
    || part.payload.episode_title
    || part.payload.chapterTitle
    || part.payload.chapter_title
    || part.payload.sceneTitle
    || part.payload.scene_title
    || part.payload.name
    || part.payload.heading
    || part.payload.slugline
  const title = typeof rawTitle === 'string' ? rawTitle.trim() : ''
  if (part.type === 'episode') {
    const prefix = `第 ${part.key} 集`
    const titleWithoutEpisode = title.replace(
      new RegExp(`^第\\s*${escapeRegExp(part.key)}\\s*集\\s*(?:[·:：—-]\\s*)?`),
      '',
    ).trim()
    return titleWithoutEpisode ? `${prefix} · ${titleWithoutEpisode}` : prefix
  }
  if (part.type === 'scene') {
    return title ? `场景 ${part.key} · ${title}` : `场景 ${part.key}`
  }
  if (part.type === 'reviewIssueGroup') {
    return title ? `问题组 ${part.key} · ${title}` : `问题组 ${part.key}`
  }
  return title || (index === 0 ? '完整文档' : part.key)
}

export function revisionPartKindLabel(part: ScreenplayV2RevisionPart): string {
  if (part.type === 'episode') return '分集文档'
  if (part.type === 'scene') return '场景文档'
  if (part.type === 'reviewIssueGroup') return '审阅问题'
  return '项目文档'
}

export function revisionPartMarkdown(part: ScreenplayV2RevisionPart): string {
  const markdown = part.contentText.trim()
  return markdown || structuredContentToMarkdown(part.payload)
}
