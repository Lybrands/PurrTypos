export interface TechniqueFileNode {
  name: string
  path: string
  kind: 'folder' | 'file'
  children: TechniqueFileNode[]
}

export function buildTechniqueFileTree(paths: readonly string[]): TechniqueFileNode[] {
  const root: TechniqueFileNode[] = []
  for (const path of new Set(paths)) {
    const parts = path.split('/')
    let siblings = root
    parts.forEach((name, index) => {
      const kind = index === parts.length - 1 ? 'file' : 'folder'
      const nodePath = parts.slice(0, index + 1).join('/')
      let node = siblings.find(item => item.path === nodePath && item.kind === kind)
      if (!node) {
        node = { name, path: nodePath, kind, children: [] }
        siblings.push(node)
      }
      siblings = node.children
    })
  }
  const sort = (nodes: TechniqueFileNode[]) => {
    nodes.sort((a, b) => {
      if (a.path === 'SKILL.md') return -1
      if (b.path === 'SKILL.md') return 1
      if (a.kind !== b.kind) return a.kind === 'folder' ? -1 : 1
      return a.name.localeCompare(b.name, 'zh-CN', { numeric: true })
    })
    nodes.forEach(node => sort(node.children))
  }
  sort(root)
  return root
}
