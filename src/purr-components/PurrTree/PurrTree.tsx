import React from 'react'
import { ChevronRightIcon, DeleteIcon, EditIcon, FileTextIcon, FolderIcon, PlusIcon } from '../icons'
import { PurrButton } from '../PurrButton'
import { PurrTooltip } from '../PurrTooltip'
import '../styles/purr.scss'
import './PurrTree.scss'

export interface PurrTreeNode {
  key: string
  label: string
  title?: string
  icon?: React.ReactNode
  suffix?: React.ReactNode
  /** 设置 children（包括空数组）表示分支节点。 */
  children?: PurrTreeNode[]
  selectable?: boolean
  disabled?: boolean
}

export interface PurrTreeProps {
  nodes: PurrTreeNode[]
  selectedKey?: string
  expandedKeys?: string[]
  defaultExpandedKeys?: string[]
  onSelect?: (key: string, node: PurrTreeNode) => void
  onExpand?: (keys: string[]) => void
  'aria-label': string
  className?: string
  fileActions?: {
    heading?: string
    disabled?: boolean
    /** 允许的新建及重命名文件后缀；可传入 `.md` 或 `md`。留空表示不限制。 */
    allowedFileExtensions?: string[]
    /** 文件名未填写后缀时自动补全；未配置时要求用户显式填写允许的后缀。 */
    defaultFileExtension?: string
    onCreateNode?: (kind: 'file' | 'folder', parentPath: string, name: string) => void | Promise<void>
    onRenameNode?: (path: string, name: string, node: PurrTreeNode) => void | Promise<void>
    onDeleteNode?: (path: string, node: PurrTreeNode) => void
    canRenameNode?: (path: string, node: PurrTreeNode) => boolean
    canDeleteNode?: (path: string, node: PurrTreeNode) => boolean
  }
}

function normalizeExtension(extension: string) {
  const value = extension.trim().toLocaleLowerCase()
  return value ? (value.startsWith('.') ? value : `.${value}`) : ''
}

export function resolveTreeFileName(name: string, allowedExtensions: string[] = [], defaultExtension?: string) {
  const allowed = [...new Set(allowedExtensions.map(normalizeExtension).filter(Boolean))]
  const fallback = defaultExtension ? normalizeExtension(defaultExtension) : ''
  if (fallback && allowed.length && !allowed.includes(fallback)) throw new Error('默认文件后缀必须包含在允许范围内')
  const suffix = name.match(/(?:^|[^.])(\.[^.]+)$/)?.[1]?.toLocaleLowerCase() ?? ''
  if (!suffix && fallback) return `${name}${fallback}`
  if (allowed.length && !suffix) throw new Error(`文件名需包含后缀：${allowed.join('、')}`)
  if (allowed.length && !allowed.includes(suffix)) throw new Error(`仅支持以下文件后缀：${allowed.join('、')}`)
  return name
}

export function PurrTree({ nodes, selectedKey, expandedKeys, defaultExpandedKeys = [], onSelect, onExpand, className, fileActions, 'aria-label': label }: PurrTreeProps) {
  const [internalExpanded, setInternalExpanded] = React.useState(defaultExpandedKeys)
  const [focusedKey, setFocusedKey] = React.useState<string>()
  const expanded = new Set(expandedKeys ?? internalExpanded)
  const elements = React.useRef(new Map<string, HTMLLIElement>())
  const editInput = React.useRef<HTMLInputElement>(null)
  const cancelInline = React.useRef(false)
  const [inlineEdit, setInlineEdit] = React.useState<{ mode: 'create' | 'rename'; kind: 'file' | 'folder'; parent: string; path?: string; node?: PurrTreeNode; value: string; error?: string }>()
  const [committing, setCommitting] = React.useState(false)
  const visible: Array<{ node: PurrTreeNode; parent?: string }> = []
  const visit = (items: PurrTreeNode[], parent?: string) => {
    items.forEach(node => {
      visible.push({ node, parent })
      if (node.children && expanded.has(node.key)) visit(node.children, node.key)
    })
  }
  visit(nodes)
  const activeKey = [focusedKey, selectedKey].find(key => visible.some(item => item.node.key === key)) ?? visible[0]?.node.key
  const focus = (key?: string) => {
    if (!key) return
    setFocusedKey(key)
    elements.current.get(key)?.focus()
  }
  const toggle = (node: PurrTreeNode) => {
    if (node.disabled || !node.children) return
    const next = new Set(expanded)
    if (next.has(node.key)) next.delete(node.key)
    else next.add(node.key)
    if (expandedKeys === undefined) setInternalExpanded([...next])
    onExpand?.([...next])
  }
  React.useEffect(() => {
    if (!inlineEdit) return
    editInput.current?.focus()
    editInput.current?.select?.()
  }, [inlineEdit?.mode, inlineEdit?.parent, inlineEdit?.path])
  const beginCreate = (kind: 'file' | 'folder', parent: string) => {
    if (fileActions?.disabled || !fileActions?.onCreateNode) return
    const parentNode = visible.find(item => item.node.key === parent)?.node
    if (parentNode?.children && !expanded.has(parent)) toggle(parentNode)
    cancelInline.current = false
    setInlineEdit({ mode: 'create', kind, parent, value: '' })
  }
  const beginRename = (node: PurrTreeNode) => {
    if (fileActions?.disabled || !fileActions?.onRenameNode) return
    cancelInline.current = false
    setInlineEdit({ mode: 'rename', kind: node.children ? 'folder' : 'file', parent: node.key.split('/').slice(0, -1).join('/') || '/', path: node.key, node, value: node.label })
  }
  const commitInlineEdit = async () => {
    if (!inlineEdit || committing) return
    let name = inlineEdit.value.trim()
    if (!name && inlineEdit.mode === 'create') {
      setInlineEdit(undefined)
      return
    }
    if (!name || name.includes('/') || name.includes('\\')) {
      setInlineEdit(current => current ? { ...current, error: '名称不能为空，也不能包含路径分隔符' } : current)
      return
    }
    if (inlineEdit.kind === 'file') {
      try { name = resolveTreeFileName(name, fileActions?.allowedFileExtensions, fileActions?.defaultFileExtension) }
      catch (error) { setInlineEdit(current => current ? { ...current, error: (error as Error).message } : current); return }
    }
    setCommitting(true)
    try {
      if (inlineEdit.mode === 'create') await fileActions?.onCreateNode?.(inlineEdit.kind, inlineEdit.parent, name)
      else if (inlineEdit.path && inlineEdit.node) await fileActions?.onRenameNode?.(inlineEdit.path, name, inlineEdit.node)
      setInlineEdit(undefined)
    } catch (error) {
      setInlineEdit(current => current ? { ...current, error: (error as Error).message } : current)
    } finally { setCommitting(false) }
  }
  const inlineRow = (parent: string, level: number) => inlineEdit?.mode === 'create' && inlineEdit.parent === parent ? <li className="purr-tree__item purr-tree__item--editing" role="treeitem" aria-level={level}>
    <div className="purr-tree__row">
      <span className="purr-tree__switcher" />
      <span className="purr-tree__icon" aria-hidden>{inlineEdit.kind === 'folder' ? <FolderIcon /> : <FileTextIcon />}</span>
      <input ref={editInput} value={inlineEdit.value} disabled={committing} aria-label={inlineEdit.kind === 'folder' ? '文件夹名称' : '文件名称'} placeholder={inlineEdit.kind === 'folder' ? '文件夹名称' : fileActions?.allowedFileExtensions?.length ? fileActions.allowedFileExtensions.map(normalizeExtension).join(' / ') : '文件名称'} onChange={event => setInlineEdit({ ...inlineEdit, value: event.target.value, error: undefined })} onKeyDown={event => {
        event.stopPropagation()
        if (event.key === 'Enter') { event.preventDefault(); void commitInlineEdit() }
        if (event.key === 'Escape') { event.preventDefault(); cancelInline.current = true; setInlineEdit(undefined) }
      }} onBlur={() => { if (cancelInline.current) cancelInline.current = false; else void commitInlineEdit() }} />
      {inlineEdit.error && <span className="purr-tree__inline-error" title={inlineEdit.error}>!</span>}
    </div>
  </li> : null
  const select = (node: PurrTreeNode) => {
    if (!node.disabled && node.selectable !== false) onSelect?.(node.key, node)
  }
  const keyDown = (event: React.KeyboardEvent, node: PurrTreeNode) => {
    event.stopPropagation()
    const index = visible.findIndex(item => item.node.key === node.key)
    switch (event.key) {
      case 'ArrowDown': focus(visible[index + 1]?.node.key); break
      case 'ArrowUp': focus(visible[index - 1]?.node.key); break
      case 'Home': focus(visible[0]?.node.key); break
      case 'End': focus(visible.at(-1)?.node.key); break
      case 'ArrowRight':
        if (node.children && !node.disabled) {
          if (!expanded.has(node.key)) toggle(node)
          else focus(node.children[0]?.key)
        }
        break
      case 'ArrowLeft':
        if (node.children && expanded.has(node.key)) toggle(node)
        else focus(visible[index]?.parent)
        break
      case 'Enter':
      case ' ':
        if (node.children) toggle(node)
        select(node)
        break
      default: return
    }
    event.preventDefault()
  }
  const nodeActions = (node: PurrTreeNode) => {
    if (!fileActions) return null
    const canRename = Boolean(fileActions.onRenameNode) && (fileActions.canRenameNode?.(node.key, node) ?? true)
    const canDelete = Boolean(fileActions.onDeleteNode) && (fileActions.canDeleteNode?.(node.key, node) ?? true)
    if (!node.children && !canRename && !canDelete || node.children && !fileActions.onCreateNode && !canRename && !canDelete) return null
    return <span className="purr-tree__node-actions" onClick={event => event.stopPropagation()} onKeyDown={event => event.stopPropagation()}>
      {node.children && fileActions.onCreateNode && <><PurrTooltip title="新建文件"><PurrButton type="text" size="small" aria-label={`在${node.key === '/' ? '根目录' : node.key}下新建文件`} icon={<PlusIcon />} disabled={fileActions.disabled} onClick={() => beginCreate('file', node.key)} /></PurrTooltip><PurrTooltip title="新建文件夹"><PurrButton type="text" size="small" aria-label={`在${node.key === '/' ? '根目录' : node.key}下新建文件夹`} icon={<FolderIcon />} disabled={fileActions.disabled} onClick={() => beginCreate('folder', node.key)} /></PurrTooltip></>}
      {canRename && <PurrTooltip title="重命名"><PurrButton type="text" size="small" aria-label={`重命名 ${node.label}`} icon={<EditIcon />} disabled={fileActions.disabled} onClick={() => beginRename(node)} /></PurrTooltip>}
      {canDelete && <PurrTooltip title="删除"><PurrButton type="text" size="small" aria-label={`删除 ${node.label}`} icon={<DeleteIcon />} disabled={fileActions.disabled} onClick={() => fileActions.onDeleteNode?.(node.key, node)} /></PurrTooltip>}
    </span>
  }
  const renderNodes = (items: PurrTreeNode[], level: number): React.ReactNode => items.map(node => <li
    key={node.key} role="treeitem" aria-label={node.label} aria-level={level}
    aria-expanded={node.children ? expanded.has(node.key) : undefined}
    aria-selected={node.selectable === false ? undefined : selectedKey === node.key}
    aria-disabled={node.disabled || undefined} tabIndex={node.key === activeKey ? 0 : -1}
    ref={element => { if (element) elements.current.set(node.key, element); else elements.current.delete(node.key) }}
    onFocus={event => { if (event.target === event.currentTarget) setFocusedKey(node.key) }}
    onKeyDown={event => keyDown(event, node)}
    className={`purr-tree__item${selectedKey === node.key ? ' is-selected' : ''}${node.disabled ? ' is-disabled' : ''}`}>
    <div className="purr-tree__row" title={node.title ?? node.label} onClick={() => { focus(node.key); if (node.children) toggle(node); select(node) }}>
      <span className={`purr-tree__switcher${expanded.has(node.key) ? ' is-expanded' : ''}`} aria-hidden>
        {node.children && <ChevronRightIcon />}
      </span>
      <span className="purr-tree__icon" aria-hidden>{node.icon ?? (node.children ? <FolderIcon /> : <FileTextIcon />)}</span>
      {inlineEdit?.mode === 'rename' && inlineEdit.path === node.key ? <><input ref={editInput} value={inlineEdit.value} disabled={committing} aria-label={`重命名 ${node.label}`} onClick={event => event.stopPropagation()} onChange={event => setInlineEdit({ ...inlineEdit, value: event.target.value, error: undefined })} onKeyDown={event => {
        event.stopPropagation()
        if (event.key === 'Enter') { event.preventDefault(); void commitInlineEdit() }
        if (event.key === 'Escape') { event.preventDefault(); cancelInline.current = true; setInlineEdit(undefined) }
      }} onBlur={() => { if (cancelInline.current) cancelInline.current = false; else void commitInlineEdit() }} />{inlineEdit.error && <span className="purr-tree__inline-error" title={inlineEdit.error}>!</span>}</> : <><span className="purr-tree__label">{node.label}</span>{node.suffix && <span className="purr-tree__suffix">{node.suffix}</span>}{nodeActions(node)}</>}
    </div>
    {node.children && expanded.has(node.key) && (node.children.length > 0 || inlineEdit?.parent === node.key) && <ul role="group" className="purr-tree__group">{inlineRow(node.key, level + 1)}{renderNodes(node.children, level + 1)}</ul>}
  </li>)
  return <div className={['purr-tree-shell', className].filter(Boolean).join(' ')}>
    {fileActions?.heading && <div className="purr-tree__heading"><span>{fileActions.heading}</span><div className="purr-tree__heading-actions">
      {fileActions.onCreateNode && <><PurrTooltip title="新建文件"><PurrButton type="text" size="small" aria-label="新建文件" icon={<PlusIcon />} disabled={fileActions.disabled} onClick={() => beginCreate('file', nodes.length === 1 && nodes[0].key === '/' ? '/' : '')} /></PurrTooltip><PurrTooltip title="新建文件夹"><PurrButton type="text" size="small" aria-label="新建文件夹" icon={<FolderIcon />} disabled={fileActions.disabled} onClick={() => beginCreate('folder', nodes.length === 1 && nodes[0].key === '/' ? '/' : '')} /></PurrTooltip></>}
    </div></div>}
    <ul role="tree" aria-label={label} className="purr-tree">{inlineRow('', 1)}{renderNodes(nodes, 1)}</ul>
  </div>
}
