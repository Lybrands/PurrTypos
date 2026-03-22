import React from 'react'
import { CloseOutlined, DownOutlined, SearchOutlined, UpOutlined } from '@ant-design/icons'
import type { InputRef } from 'antd/es/input/Input'
import { Button, Input, Tooltip } from 'antd'
import { useWorkspace } from './WorkspaceContext'
import './workspaceSearch.scss'

/** 占位符提示：Mac/iOS 为 ⌘F，Windows/Linux 为 Ctrl+F */
function getFindShortcutLabel(): string {
  if (typeof navigator === 'undefined') return 'Ctrl+F'
  const isApple =
    /Mac|iPhone|iPad|iPod/.test(navigator.platform) || /Mac OS X/.test(navigator.userAgent)
  return isApple ? '⌘F' : 'Ctrl+F'
}

export interface WorkspaceSearchPanelProps {
  /**
   * true（默认）：`position:fixed`，贴在视口右上角、顶栏下方。
   * false：由父级提供 `position:relative` 容器，浮层 `absolute` 贴容器右上角（便于自定义嵌入位置）。
   */
  fixed?: boolean
  className?: string
}

/**
 * 工作台全局搜索：默认隐藏，Ctrl+F / ⌘F 打开；关闭按钮或 Escape 收起。
 * 可放在 `Workspace` 内任意位置；`fixed={false}` 时需外层 `position:relative`。
 */
export default function WorkspaceSearchPanel({ fixed = true, className }: WorkspaceSearchPanelProps) {
  const {
    workspaceSearchQuery,
    setWorkspaceSearchQuery,
    workspaceSearchActiveIndex,
    goToNextWorkspaceSearch,
    goToPrevWorkspaceSearch,
    workspaceSearchMatchTotal,
  } = useWorkspace()

  const [open, setOpen] = React.useState(false)
  const inputRef = React.useRef<InputRef>(null)
  const findShortcutLabel = React.useMemo(() => getFindShortcutLabel(), [])

  React.useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'f' && e.key !== 'F') return
      if (!(e.ctrlKey || e.metaKey)) return
      if (e.altKey) return
      e.preventDefault()
      e.stopPropagation()
      setOpen(true)
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [])

  React.useEffect(() => {
    if (!open) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      e.stopPropagation()
      setOpen(false)
    }
    window.addEventListener('keydown', onEsc, true)
    return () => window.removeEventListener('keydown', onEsc, true)
  }, [open])

  React.useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    }, 0)
    return () => window.clearTimeout(t)
  }, [open])

  const total = workspaceSearchMatchTotal
  const q = workspaceSearchQuery.trim()
  const showCount = q.length > 0 && total > 0

  if (!open) return null

  return (
    <div
      className={[
        'workspace-search-floating',
        fixed ? 'workspace-search-floating--fixed' : 'workspace-search-floating--anchored',
        className ?? '',
      ]
        .filter(Boolean)
        .join(' ')}
      role="search"
    >
      <div className="workspace-search-bar">
        <Input
          ref={inputRef}
          className="workspace-search-input"
          allowClear
          placeholder={`搜索大纲 / 小说背景 / 正文…（${findShortcutLabel}）`}
          aria-label={`搜索大纲、小说背景与正文，快捷键 ${findShortcutLabel}`}
          prefix={<SearchOutlined className="workspace-search-prefix-icon" />}
          value={workspaceSearchQuery}
          onChange={(e) => setWorkspaceSearchQuery(e.target.value)}
          onPressEnter={() => goToNextWorkspaceSearch()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.shiftKey) {
              e.preventDefault()
              goToPrevWorkspaceSearch()
            }
          }}
        />
        {showCount && (
          <span className="workspace-search-count" aria-live="polite">
            {workspaceSearchActiveIndex + 1}/{total}
          </span>
        )}
        <Tooltip title="上一个 (Shift+Enter)">
          <Button
            type="text"
            size="small"
            className="workspace-search-nav-btn"
            icon={<UpOutlined style={{ fontSize: 14 }} />}
            onClick={() => goToPrevWorkspaceSearch()}
            disabled={!q || total === 0}
          />
        </Tooltip>
        <Tooltip title="下一个 (Enter)">
          <Button
            type="text"
            size="small"
            className="workspace-search-nav-btn"
            icon={<DownOutlined style={{ fontSize: 14 }} />}
            onClick={() => goToNextWorkspaceSearch()}
            disabled={!q || total === 0}
          />
        </Tooltip>
        <Tooltip title="关闭 (Esc)">
          <Button
            type="text"
            size="small"
            className="workspace-search-nav-btn"
            icon={<CloseOutlined style={{ fontSize: 14 }} />}
            onClick={() => setOpen(false)}
            aria-label="关闭搜索"
          />
        </Tooltip>
      </div>
    </div>
  )
}
