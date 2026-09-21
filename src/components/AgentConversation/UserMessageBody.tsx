import './UserMessageBody.scss'
import { splitUserQuote } from './userQuote'

export interface AgentUserMessageBodyProps {
  content: string
}

/**
 * 普通用户消息的共享气泡。行首 `> ` 引用行不在气泡内展示；
 * 引用通过消息底部操作行的图标以浮层查看，见 MessageQuoteButton。
 */
export default function AgentUserMessageBody({
  content,
}: AgentUserMessageBodyProps) {
  if (!content) return null
  const { body } = splitUserQuote(content)
  return (
    <div className="bubble-content agent-user-message__content">{body}</div>
  )
}
