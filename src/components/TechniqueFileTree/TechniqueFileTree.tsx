import React from 'react'
import { PurrTree, type PurrTreeNode, type PurrTreeProps } from '@/purr-components'
import { buildTechniqueFileTree, type TechniqueFileNode } from './fileTree'

function toTreeNodes(nodes: TechniqueFileNode[], selectableFolders: boolean): PurrTreeNode[] {
  return nodes.map(node => ({
    key: node.path,
    label: node.name,
    title: node.path,
    selectable: selectableFolders || node.kind === 'file',
    children: node.kind === 'folder' ? toTreeNodes(node.children, selectableFolders) : undefined,
    suffix: node.path === 'SKILL.md' ? '入口' : undefined,
  }))
}

export default function TechniqueFileTree({ paths, selectedPath, onSelect, rootLabel, onFolderSelect, fileActions }: {
  paths: string[]
  selectedPath: string
  onSelect: (path: string) => void
  rootLabel?: string
  fileActions?: PurrTreeProps['fileActions']
  onFolderSelect?: (path: string) => void
}) {
  const nodes = React.useMemo(() => {
    const children = toTreeNodes(buildTechniqueFileTree(paths), Boolean(onFolderSelect))
    return rootLabel ? [{ key: '/', label: rootLabel, children, selectable: Boolean(onFolderSelect) }] : children
  }, [paths, rootLabel, onFolderSelect])
  const [collapsed, setCollapsed] = React.useState<Set<string>>(() => new Set())
  React.useEffect(() => {
    setCollapsed(previous => new Set([...previous].filter(folder => folder !== '/' && !selectedPath.startsWith(`${folder}/`))))
  }, [selectedPath])
  const folders: string[] = []
  const visit = (items: PurrTreeNode[]) => items.forEach(node => {
    if (node.children) { folders.push(node.key); visit(node.children) }
  })
  visit(nodes)
  return <PurrTree className="technique-file-tree" aria-label="技法文件树" nodes={nodes} fileActions={fileActions}
    selectedKey={selectedPath} onSelect={(key, node) => { if (node.children) onFolderSelect?.(key); else onSelect(key) }}
    expandedKeys={folders.filter(folder => !collapsed.has(folder))}
    onExpand={keys => {
      const expanded = new Set(keys)
      setCollapsed(new Set(folders.filter(folder => !expanded.has(folder))))
    }} />
}
