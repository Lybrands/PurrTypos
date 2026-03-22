/**
 * XMind 解析与大纲标题等纯函数
 */

export interface XmindNode {
  title?: string
  children?: { attached?: XmindNode[] }
}

export interface FlatChapter {
  title: string
  level: number
  progress: string
  sort: number
}

export function flattenXmindNodes(
  node: XmindNode,
  level = 1,
  sort = { val: 0 }
): FlatChapter[] {
  const chapters: FlatChapter[] = []
  if (!node) return chapters
  chapters.push({
    title: node.title || '（无标题）',
    level,
    progress: 'todo',
    sort: sort.val++,
  })
  if (node.children?.attached) {
    for (const child of node.children.attached) {
      chapters.push(...flattenXmindNodes(child, level + 1, sort))
    }
  }
  return chapters
}

export function parseXmindToChapters(parseRes: {
  success: boolean
  data: any[]
}): FlatChapter[] {
  if (!parseRes.success || !parseRes.data?.[0]) return []
  const rootTopic = parseRes.data[0]?.rootTopic
  if (!rootTopic?.children?.attached) return []
  const flatChapters: FlatChapter[] = []
  const sort = { val: 0 }
  for (const child of rootTopic.children.attached) {
    flatChapters.push(...flattenXmindNodes(child, 1, sort))
  }
  return flatChapters
}

export function getTitleFromXmind(
  parseRes: { success: boolean; data: any[] },
  filePath: string
): string {
  if (!parseRes.success || !parseRes.data?.[0]) return '未命名'
  const rootTopic = parseRes.data[0]?.rootTopic
  return (
    rootTopic?.title ||
    getFileNameFromPath(filePath) ||
    '未命名'
  )
}

export function getFileNameFromPath(filePath: string): string {
  return filePath.split(/[/\\]/).pop()?.replace(/\.xmind$/i, '') || '未命名'
}
