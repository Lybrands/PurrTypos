/**
 * 思维导图数据转换：章节列表 / XMind JSON 转为 simple-mind-map 所需格式
 */

import type { Chapter } from '../types'
import { shortUuid } from './common'

export function chaptersToMindMapData(chapters: Chapter[], rootTitle: string): any {
  interface StackItem {
    node: any
    level: number
  }
  const root: any = { data: { text: rootTitle }, children: [] }
  const stack: StackItem[] = [{ node: root, level: 0 }]

  for (const ch of chapters) {
    const level = ch?.level ?? 1
    const newNode: any = { data: { text: ch?.title ?? '（无标题）' }, children: [] }

    while (stack.length > 1 && stack[stack.length - 1].level >= level) {
      stack.pop()
    }
    stack[stack.length - 1].node.children.push(newNode)
    stack.push({ node: newNode, level })
  }
  return root
}

export function convertXmindNode(xmindNode: any, idMap: Map<string, string>): any {
  const uid = `uid_${shortUuid()}`
  const data: any = { text: xmindNode.title || '', uid }

  if (xmindNode.id) {
    idMap.set(xmindNode.id, uid)
  }

  if (xmindNode.notes) {
    const plain = xmindNode.notes.plain?.content
    if (plain) {
      data.note = plain
    } else {
      const html = xmindNode.notes.realHTML?.content
      if (html) data.note = html.replace(/<[^>]*>/g, '')
    }
  }

  if (Array.isArray(xmindNode.labels) && xmindNode.labels.length > 0) {
    data.tag = xmindNode.labels
  }

  if (xmindNode.href && /^https?:\/\//.test(xmindNode.href)) {
    data.hyperlink = xmindNode.href
  }

  const children: any[] = []
  const attached = xmindNode.children?.attached
  if (Array.isArray(attached)) {
    for (const child of attached) {
      children.push(convertXmindNode(child, idMap))
    }
  }
  return { data, children }
}

export function applyRelationships(
  root: any,
  relationships: any[],
  idMap: Map<string, string>
): void {
  if (!Array.isArray(relationships) || relationships.length === 0) return

  const uidToNode = new Map<string, any>()
  const walk = (node: any) => {
    if (node?.data?.uid) uidToNode.set(node.data.uid, node)
    node?.children?.forEach((c: any) => walk(c))
  }
  walk(root)

  for (const rel of relationships) {
    const fromUid = idMap.get(rel.end1Id)
    const toUid = idMap.get(rel.end2Id)
    if (!fromUid || !toUid) continue
    const fromNode = uidToNode.get(fromUid)
    if (!fromNode) continue

    if (!fromNode.data.associativeLineTargets) {
      fromNode.data.associativeLineTargets = []
    }
    fromNode.data.associativeLineTargets.push(toUid)

    if (rel.title) {
      if (!fromNode.data.associativeLineText) {
        fromNode.data.associativeLineText = []
      }
      fromNode.data.associativeLineText[fromNode.data.associativeLineTargets.length - 1] = rel.title
    }
  }
}

export function convertXmindJson(rawJson: string): any {
  const parsed = JSON.parse(rawJson)
  const sheet = Array.isArray(parsed) ? parsed[0] : parsed
  const rootTopic = sheet?.rootTopic
  if (!rootTopic) return null

  const idMap = new Map<string, string>()
  const tree = convertXmindNode(rootTopic, idMap)
  const relationships = sheet?.relationships || []
  applyRelationships(tree, relationships, idMap)
  return tree
}

export function countNodes(node: any): number {
  return 1 + (node?.children || []).reduce((s: number, c: any) => s + countNodes(c), 0)
}

export function countField(node: any, field: string): number {
  const has = node?.data?.[field] ? 1 : 0
  return has + (node?.children || []).reduce((s: number, c: any) => s + countField(c, field), 0)
}
