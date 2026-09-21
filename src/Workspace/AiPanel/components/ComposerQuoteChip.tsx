/**
 * 输入框上方的「引用」胶囊：正文选区点「引用」后累积（多条合并到同一枚），
 * 悬停展开全部引用内容，展开面板里可逐条删除，胶囊本身可一键清空。
 * 发送时由 AiPanel 把引用拼进消息。
 */

import { CloseIcon, LinkIcon, PurrButton, PurrPopover } from '@/purr-components'
import type { UserQuoteInput } from '@/components/AgentConversation/userQuote'

export default function ComposerQuoteChip({
  quotes,
  onRemoveAt,
  onRemoveAll,
}: {
  quotes: UserQuoteInput[]
  onRemoveAt: (index: number) => void
  onRemoveAll: () => void
}) {
  if (quotes.length === 0) return null
  const single = quotes[0]
  const title = single.chapterTitle?.trim()
  const excerpt = single.quote.replace(/\s+/g, ' ').trim().slice(0, 10)
  const label =
    quotes.length > 1
      ? `引用 ×${quotes.length}`
      : title
        ? `引用《${title}》`
        : excerpt
          ? `引用「${excerpt}${single.quote.length > 10 ? '…' : ''}」`
          : '引用正文'

  return (
    <div className="composer-quote-chip">
      <PurrPopover
        trigger="hover"
        placement="topLeft"
        mouseEnterDelay={0.12}
        mouseLeaveDelay={0.2}
        nativeButton={false}
        maxHeight={320}
        content={
          <div className="composer-quote-chip__preview">
            {quotes.map((q, index) => (
              <div key={index} className="composer-quote-chip__preview-item">
                <div className="composer-quote-chip__preview-head">
                  {q.chapterTitle?.trim() ? (
                    <span className="composer-quote-chip__preview-title">《{q.chapterTitle.trim()}》</span>
                  ) : <span className="composer-quote-chip__preview-title">引用</span>}
                  <PurrButton
                    type="text"
                    size="small"
                    className="composer-quote-chip__item-remove"
                    aria-label="删除此条引用"
                    icon={<CloseIcon style={{ fontSize: 12 }} />}
                    onClick={() => onRemoveAt(index)}
                  />
                </div>
                <blockquote className="composer-quote-chip__preview-quote">{q.quote}</blockquote>
              </div>
            ))}
          </div>
        }
      >
        <span className="composer-quote-chip__label">
          <LinkIcon size={13} />
          {label}
        </span>
      </PurrPopover>
      <PurrButton
        type="text"
        size="small"
        className="composer-quote-chip__remove"
        aria-label="清空引用"
        icon={<CloseIcon style={{ fontSize: 12 }} />}
        onClick={onRemoveAll}
      />
    </div>
  )
}
