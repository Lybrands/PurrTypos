import './UserMessageBody.scss'
import { splitUserQuote } from './userQuote'

export interface AgentUserMessageBodyProps {
  content: string
}

/** 普通用户消息的共享气泡；行首连续 `> ` 行渲染为选区引用块。 */
export default function AgentUserMessageBody({
  content,
}: AgentUserMessageBodyProps) {
  if (!content) return null
  const { quoteLines, body } = splitUserQuote(content)
  return (
    <div className="bubble-content agent-user-message__content">
      {quoteLines.length > 0 && (
        <blockquote className="agent-user-message__quote">
          {quoteLines.join('\n')}
        </blockquote>
      )}
      {body}
    </div>
  )
}
