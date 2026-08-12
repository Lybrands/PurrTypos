import React from 'react'
import { PurrPopover } from '@/purr-components'
import type { AgentConversationMessage } from '../../../agent-runtime'
import Markdown from '../../Markdown'
import './index.scss'

export interface AgentConversationTurnIndexItem {
  dataIndex: number
  userText: string
  userMarkdown: string
  assistantText: string
  assistantMarkdown: string
}

interface AgentConversationTurnIndexProps {
  items: AgentConversationTurnIndexItem[]
  activeIndex: number
  onSelect: (item: AgentConversationTurnIndexItem) => void
}

function plainPreview(value: string | undefined, fallback: string, maxLength: number) {
  const normalized = (value ?? '')
    .slice(0, maxLength * 4)
    .replace(/```[\s\S]*?```/g, ' 代码片段 ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' 图片 ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]\s|\d+[.)]\s)\s*/gm, '')
    .replace(/[*_~]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
  return normalized ? normalized.slice(0, maxLength) : fallback
}

function markdownPreview(value: string | undefined, fallback: string, maxLength: number) {
  const normalized = (value ?? '').trim()
  if (!normalized) return fallback
  if (normalized.length <= maxLength) return normalized
  const candidate = normalized.slice(0, maxLength)
  const boundary = Math.max(
    candidate.lastIndexOf('\n\n'),
    candidate.lastIndexOf('\n'),
    candidate.lastIndexOf(' '),
  )
  let preview = candidate.slice(
    0,
    boundary >= Math.floor(maxLength * 0.6) ? boundary : maxLength,
  ).trimEnd()
  if ((preview.match(/^```/gm) ?? []).length % 2 !== 0) preview += '\n```'
  return `${preview}\n\n…`
}

export function buildAgentConversationTurnIndex(
  messages: AgentConversationMessage[],
): AgentConversationTurnIndexItem[] {
  const turns: AgentConversationTurnIndexItem[] = []
  messages.forEach((message, dataIndex) => {
    if (message.role !== 'user') return
    let assistant: AgentConversationMessage | undefined
    for (let index = dataIndex + 1; index < messages.length; index += 1) {
      const candidate = messages[index]
      if (candidate.role === 'user') break
      if (candidate.role === 'assistant') {
        assistant = candidate
        break
      }
    }
    const assistantSource = assistant?.content
      || assistant?.commentary
      || assistant?.toolCallSegments?.flatMap((segment) => segment.labels).join('、')
    turns.push({
      dataIndex,
      userText: plainPreview(message.content, '未命名提问', 180),
      userMarkdown: markdownPreview(message.content, '未命名提问', 1000),
      assistantText: plainPreview(
        assistantSource,
        assistant ? 'AI 正在整理回复…' : '等待 AI 回复…',
        240,
      ),
      assistantMarkdown: markdownPreview(
        assistantSource,
        assistant ? 'AI 正在整理回复…' : '等待 AI 回复…',
        2400,
      ),
    })
  })
  return turns
}

const POPOVER_STYLES = {
  root: { width: 320, maxWidth: 'calc(100vw - 32px)' },
  container: {
    boxSizing: 'border-box' as const,
    width: '100%',
    maxWidth: '100%',
    maxHeight: 220,
    overflow: 'hidden',
  },
  content: { width: '100%', maxWidth: '100%', maxHeight: 192, overflow: 'hidden' },
}

export default function AgentConversationTurnIndex({
  items,
  activeIndex,
  onSelect,
}: AgentConversationTurnIndexProps) {
  const listRef = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    const list = listRef.current
    const activeItem = list?.querySelector<HTMLElement>('[data-active="true"]')
    if (!list || !activeItem) return
    const itemTop = activeItem.offsetTop
    const itemBottom = itemTop + activeItem.offsetHeight
    if (itemTop < list.scrollTop) list.scrollTop = Math.max(0, itemTop - 8)
    else if (itemBottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = itemBottom - list.clientHeight + 8
    }
  }, [activeIndex])

  if (items.length < 2) return null

  return (
    <nav className="chat-turn-index" aria-label="对话索引">
      <div className="chat-turn-index-list" ref={listRef}>
        {items.map((item, index) => {
          const isActive = index === activeIndex
          return (
            <PurrPopover
              key={`${item.dataIndex}-${index}`}
              placement="right"
              trigger="hover"
              mouseEnterDelay={0.08}
              mouseLeaveDelay={0.06}
              arrow={{ pointAtCenter: true }}
              destroyOnHidden
              classNames={{ root: 'chat-turn-index-popover' }}
              styles={POPOVER_STYLES}
              content={(
                <div className="chat-turn-index-preview">
                  <div className="chat-turn-index-preview-meta">第 {index + 1} 轮对话</div>
                  <div className="chat-turn-index-preview-question"><Markdown>{item.userMarkdown}</Markdown></div>
                  <div className="chat-turn-index-preview-answer"><Markdown>{item.assistantMarkdown}</Markdown></div>
                </div>
              )}
            >
              <button
                type="button"
                className={`chat-turn-index-item${isActive ? ' is-active' : ''}`}
                data-active={isActive ? 'true' : 'false'}
                aria-label={`跳转到第 ${index + 1} 轮对话：${item.userText}`}
                aria-current={isActive ? 'location' : undefined}
                onClick={(event) => {
                  event.currentTarget.blur()
                  onSelect(item)
                }}
              >
                <span className="chat-turn-index-mark" />
              </button>
            </PurrPopover>
          )
        })}
      </div>
    </nav>
  )
}
