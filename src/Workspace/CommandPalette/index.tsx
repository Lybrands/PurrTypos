/**
 * ⌘K 全局命令面板。
 * - 纯 UI：接收 commands 数组 + open 状态，执行命令后自动关闭。
 * - 命令项：`{ id, label, hint?, icon?, keywords?, category?, run }`。
 * - 搜索策略：大小写无关的子串匹配，匹配 label、hint、keywords、category。
 * - 键盘：↑↓ 移动，Enter 执行，Esc 关闭。
 */

import React from 'react'
import { PurrInput, type PurrInputRef } from '@/purr-components'
import './CommandPalette.scss'

export interface CommandItem {
  id: string
  label: string
  hint?: string
  icon?: React.ReactNode
  keywords?: string[]
  category?: string
  run: () => void | Promise<void>
}

interface CommandPaletteProps {
  open: boolean
  commands: CommandItem[]
  onClose: () => void
  placeholder?: string
}

export default function CommandPalette({
  open,
  commands,
  onClose,
  placeholder = '搜索命令、章节、动作...  (Esc 关闭)',
}: CommandPaletteProps) {
  const [query, setQuery] = React.useState('')
  const [activeIndex, setActiveIndex] = React.useState(0)
  const inputRef = React.useRef<PurrInputRef>(null)
  const listRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    if (open) {
      setQuery('')
      setActiveIndex(0)
      setTimeout(() => inputRef.current?.focus(), 40)
    }
  }, [open])

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return commands
    return commands.filter((c) => {
      const hay = [
        c.label,
        c.hint ?? '',
        c.category ?? '',
        ...(c.keywords ?? []),
      ]
        .join(' ')
        .toLowerCase()
      return hay.includes(q)
    })
  }, [commands, query])

  React.useEffect(() => {
    setActiveIndex(0)
  }, [query])

  /** 保持选中项可见 */
  React.useEffect(() => {
    const root = listRef.current
    if (!root) return
    const active = root.querySelector<HTMLDivElement>('.cmdk-item-active')
    if (!active) return
    active.scrollIntoView({ block: 'nearest' })
  }, [activeIndex, filtered])

  const runCommand = React.useCallback(
    async (cmd: CommandItem) => {
      onClose()
      // 异步关闭后再执行，避免命令内部 setState 与 palette unmount 冲突
      queueMicrotask(() => {
        void cmd.run()
      })
    },
    [onClose]
  )

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActiveIndex((i) => Math.min(filtered.length - 1, i + 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActiveIndex((i) => Math.max(0, i - 1))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const cmd = filtered[activeIndex]
      if (cmd) void runCommand(cmd)
    }
  }

  if (!open) return null

  // 按分类分组显示
  const groups = new Map<string, { start: number; items: CommandItem[] }>()
  filtered.forEach((cmd, i) => {
    const cat = cmd.category || '常用'
    if (!groups.has(cat)) groups.set(cat, { start: i, items: [] })
    groups.get(cat)!.items.push(cmd)
  })

  let globalIndex = -1

  return (
    <div className="cmdk-mask" onMouseDown={onClose}>
      <div
        className="cmdk-dialog"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="cmdk-input-wrap">
          <PurrInput
            ref={inputRef}
            className="cmdk-input"
            variant="borderless"
            placeholder={placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <span className="cmdk-hint-kbd">⌘K</span>
        </div>

        <div className="cmdk-list" ref={listRef}>
          {filtered.length === 0 ? (
            <div className="cmdk-empty">没有匹配的命令</div>
          ) : (
            Array.from(groups.entries()).map(([cat, info]) => (
              <div key={cat} className="cmdk-group">
                <div className="cmdk-group-title">{cat}</div>
                {info.items.map((cmd) => {
                  globalIndex += 1
                  const isActive = globalIndex === activeIndex
                  return (
                    <div
                      key={cmd.id}
                      className={`cmdk-item ${isActive ? 'cmdk-item-active' : ''}`}
                      onMouseMove={() => {
                        // 捕获闭包值
                        const idx = filtered.indexOf(cmd)
                        if (idx !== activeIndex) setActiveIndex(idx)
                      }}
                      onClick={() => void runCommand(cmd)}
                    >
                      <span className="cmdk-item-icon">{cmd.icon}</span>
                      <span className="cmdk-item-label">{cmd.label}</span>
                      {cmd.hint && (
                        <span className="cmdk-item-hint">{cmd.hint}</span>
                      )}
                    </div>
                  )
                })}
              </div>
            ))
          )}
        </div>

        <div className="cmdk-footer">
          <span>
            <kbd>↑↓</kbd> 选择
          </span>
          <span>
            <kbd>Enter</kbd> 执行
          </span>
          <span>
            <kbd>Esc</kbd> 关闭
          </span>
        </div>
      </div>
    </div>
  )
}
