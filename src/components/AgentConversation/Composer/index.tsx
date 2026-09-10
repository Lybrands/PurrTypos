import React from 'react'
import { PurrButton, PurrInput, PurrTooltip, PlusIcon } from '@/purr-components'
import type { AgentComposerCommand, AgentComposerActionMenu } from '../extensions'
import './index.scss'

export interface AgentComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
  placeholder?: string
  ariaLabel?: string
  disabled?: boolean
  submitDisabled?: boolean
  autoSize?: { minRows?: number; maxRows?: number }
  supplementaryContent?: React.ReactNode
  floatingContent?: React.ReactNode
  footer: React.ReactNode
  className?: string
  commands?: AgentComposerCommand[]
  actionMenu?: AgentComposerActionMenu
}

export function composerCommandQuery(value: string): string | null {
  const match = String(value ?? '').match(/^\s*\/([^\s]*)$/)
  return match ? match[1].toLocaleLowerCase() : null
}

export function composerActionTrigger(previous: string, next: string, caret: number, triggers: string[], composing = false): string | undefined {
  if (composing || next.length <= previous.length) return undefined
  return triggers.filter(Boolean).slice().sort((a, b) => b.length - a.length)
    .find(token => next.slice(0, caret).endsWith(token)
      && (caret === token.length || /\s/.test(next[caret - token.length - 1])))
}

export function composerActionDismissal(
  next: string,
  range: { start: number; token: string } | null,
): 'none' | 'deleted' | 'literal' {
  if (!range) return 'none'
  if (next.slice(range.start, range.start + range.token.length) !== range.token) return 'deleted'
  return /\s/.test(next[range.start + range.token.length] ?? '') ? 'literal' : 'none'
}

export function composerActionCloseValue(
  value: string,
  range: { start: number; token: string } | null,
  consumeTrigger: boolean,
): string {
  if (!consumeTrigger || !range) return value
  const { start, token } = range
  if (value.slice(start, start + token.length) !== token) return value
  const tail = value.slice(start + token.length)
  const queryLength = tail.match(/^\S*/)?.[0].length ?? 0
  return value.slice(0, start) + tail.slice(queryLength)
}

/**
 * Agent 对话的共享输入器。
 *
 * 组件只管理输入卡片、自动增高与键盘提交；模型、上下文、停止和发送等
 * 业务操作由 footer/floatingContent 插槽传入，以便不同 Agent 复用同一形态。
 */
export default function AgentComposer({
  value,
  onChange,
  onSubmit,
  placeholder = '想写点什么',
  ariaLabel = '输入对话内容',
  disabled = false,
  submitDisabled = false,
  autoSize = { minRows: 1, maxRows: 5 },
  supplementaryContent,
  floatingContent,
  footer,
  className,
  commands = [],
  actionMenu,
}: AgentComposerProps) {
  const [menuOpen, setMenuOpen] = React.useState(false)
  const triggerRange = React.useRef<{ start: number; token: string } | null>(null)
  const container = React.useRef<HTMLDivElement>(null)
  const menu = React.useRef<HTMLDivElement>(null)
  React.useEffect(() => {
    if (!menuOpen) return
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !container.current?.contains(event.target)
        && !(event.target instanceof Element && event.target.closest('[role="dialog"], [role="listbox"], .purr-popover, .purr-select__popup'))) closeMenu(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [menuOpen, value])
  const focusAction = (direction: number) => {
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('button:not(:disabled), [role="combobox"]:not([aria-disabled="true"])') ?? [])
      .filter(item => item.getClientRects().length > 0)
    const index = items.indexOf(document.activeElement as HTMLElement)
    items[index < 0 ? (direction > 0 ? 0 : items.length - 1) : (index + direction + items.length) % items.length]?.focus()
  }
  const closeMenu = (restoreFocus = true, consumeTrigger = false) => {
    setMenuOpen(false)
    const next = composerActionCloseValue(value, triggerRange.current, consumeTrigger)
    if (next !== value) onChange(next)
    triggerRange.current = null
    if (restoreFocus) requestAnimationFrame(() => container.current?.querySelector('textarea')?.focus())
  }
  const query = composerCommandQuery(value)
  const visibleCommands = query == null ? [] : commands.filter((command) => {
    const haystack = [command.label, command.description, ...(command.keywords ?? [])]
      .filter(Boolean)
      .join(' ')
      .toLocaleLowerCase()
    return !query || haystack.includes(query)
  })
  const selectCommand = (command: AgentComposerCommand) => {
    if (disabled || command.disabled) return
    closeMenu(true, true)
    if (!triggerRange.current && !menuOpen) onChange('')
    command.onSelect()
  }
  return (
    <div
      ref={container}
      className={[
        'agent-composer',
        floatingContent ? 'agent-composer--with-floating' : '',
        disabled ? 'is-disabled' : '',
        className,
      ].filter(Boolean).join(' ')}
    >
      {floatingContent ? (
        <div className="agent-composer__floating">
          {floatingContent}
        </div>
      ) : null}
      <PurrInput.TextArea
        className="agent-composer__input"
        value={value}
        onChange={(event) => {
          const next = event.target.value
          const caret = event.target.selectionStart ?? next.length
          const dismissal = composerActionDismissal(next, triggerRange.current)
          if (menuOpen && dismissal !== 'none') {
            triggerRange.current = null
            setMenuOpen(false)
            onChange(next)
            return
          }
          const trigger = composerActionTrigger(value, next, caret, actionMenu?.triggers ?? [], (event.nativeEvent as InputEvent).isComposing)
          if (trigger) {
            triggerRange.current = { start: caret - trigger.length, token: trigger }
            onChange(next)
            setMenuOpen(true)
          } else onChange(next)
        }}
        placeholder={placeholder}
        aria-label={ariaLabel}
        autoSize={autoSize}
        disabled={disabled}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return
          if (menuOpen && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
            event.preventDefault()
            focusAction(event.key === 'ArrowDown' ? 1 : -1)
            return
          }
          if (menuOpen && event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault()
            focusAction(1)
            return
          }
          if (menuOpen && event.key === 'Escape') {
            event.preventDefault()
            closeMenu()
            return
          }
          if (event.key === 'Escape' && visibleCommands.length > 0) {
            event.preventDefault()
            onChange(value.replace(/^\s*\//, ''))
            return
          }
          if (
            event.key !== 'Enter'
            || event.shiftKey
            || event.nativeEvent.isComposing
            || disabled
            || submitDisabled
          ) return
          event.preventDefault()
          if (visibleCommands.length > 0) {
            const first = visibleCommands.find((command) => !command.disabled)
            if (first) selectCommand(first)
            return
          }
          onSubmit()
        }}
      />
      {!menuOpen && visibleCommands.length > 0 ? (
        <div className="agent-composer__command-menu" role="listbox" aria-label="输入命令">
          {visibleCommands.map((command) => (
            <button
              key={command.id}
              type="button"
              role="option"
              aria-selected={Boolean(command.active)}
              disabled={disabled || command.disabled}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectCommand(command)}
            >
              <strong>{command.label}</strong>
              {command.description ? <span>{command.description}</span> : null}
            </button>
          ))}
        </div>
      ) : null}
      {supplementaryContent}
      {actionMenu && menuOpen && <div
        ref={menu}
        className="agent-composer__action-menu"
        role="region"
        aria-label={actionMenu.title || '对话操作'}
        onKeyDown={event => {
          if (!menu.current?.contains(event.target as Node) || event.nativeEvent.isComposing) return
          if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu() }
          if ((event.key === 'ArrowDown' || event.key === 'ArrowUp')
            && (event.target as HTMLElement).getAttribute('role') !== 'combobox') {
            event.preventDefault()
            focusAction(event.key === 'ArrowDown' ? 1 : -1)
          }
        }}
      >
        <div className="agent-composer__action-menu-heading"><strong>{actionMenu.title || '对话操作'}</strong></div>
        {query ? <div className="agent-composer__action-content">
          {visibleCommands.length ? visibleCommands.map(command => <button type="button" key={command.id} disabled={command.disabled}
            className="agent-composer__search-action" onClick={() => selectCommand(command)}><strong>{command.label}</strong><span>{command.description}</span></button>)
            : <span>没有匹配的操作</span>}
        </div> : <div className="agent-composer__action-content">{actionMenu.render({ close: () => closeMenu(true, true) })}</div>}
      </div>}
      <div className="agent-composer__footer">
        {actionMenu && <PurrTooltip title={`${actionMenu.buttonLabel || '对话操作'}（输入 ${actionMenu.triggers.join(' 或 ')}）`}>
          <PurrButton type="text" size="small" aria-label={actionMenu.buttonLabel || '对话操作'} icon={<PlusIcon />} disabled={disabled} onClick={() => setMenuOpen(true)} />
        </PurrTooltip>}
        {footer}
      </div>
    </div>
  )
}
