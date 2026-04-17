/**
 * 编辑器 Ghost Text 续写（类 Copilot 体验）。
 *
 * - `trigger` 由父组件在选区空闲 800ms 时注入 `{ prefix, cursorRect }`。
 * - 组件内部发起 `aiChatStream` 流式调用，累加文本并渲染半透明虚影 overlay。
 * - **只保留首行**（遇 `\n` 截断）：避免多行位移光标定位；多行续写留给未来。
 * - 键盘：`Tab` 接受（插入到父组件提供的 accept 回调）、`Esc` 取消。
 * - 父组件通过 `triggerToken` 区分每次 idle 请求，toggle null 表示外部 cancel。
 */

import React from 'react'
import type { AiModelConfig, EntityId } from '../../types'
import './GhostCompletion.scss'

export interface GhostTrigger {
  token: number
  prefix: string
  cursorRect: DOMRect
}

interface GhostCompletionProps {
  trigger: GhostTrigger | null
  model: AiModelConfig | undefined
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  bookTitle?: string
  onAccept: (text: string) => void
  onCancel: () => void
}

export default function GhostCompletion({
  trigger,
  model,
  bookId,
  chapterId,
  chapterTitle,
  bookTitle,
  onAccept,
  onCancel,
}: GhostCompletionProps) {
  const [ghostText, setGhostText] = React.useState('')
  const [rect, setRect] = React.useState<DOMRect | null>(null)
  const chunkUnsubRef = React.useRef<(() => void) | null>(null)
  const currentTokenRef = React.useRef<number | null>(null)
  const ghostTextRef = React.useRef('')

  // 保持最新 ghostText 给 keydown listener 用
  React.useEffect(() => {
    ghostTextRef.current = ghostText
  }, [ghostText])

  const cleanup = React.useCallback(() => {
    chunkUnsubRef.current?.()
    chunkUnsubRef.current = null
    window.electronAPI.abortAiStream?.()
  }, [])

  const clearGhost = React.useCallback(() => {
    cleanup()
    setGhostText('')
    setRect(null)
    currentTokenRef.current = null
  }, [cleanup])

  // trigger 变化：null → 外部取消；非 null → 发起新请求
  React.useEffect(() => {
    if (!trigger) {
      clearGhost()
      return
    }
    if (!model?.apiKey?.trim()) return
    // 同一个 token 不重发
    if (currentTokenRef.current === trigger.token) return
    currentTokenRef.current = trigger.token

    cleanup()
    setGhostText('')
    setRect(trigger.cursorRect)

    // 注册 chunk 订阅
    const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      if (chunk.error) {
        clearGhost()
        return
      }
      if (chunk.delta) {
        setGhostText((prev) => {
          // 遇到换行就停止累加，保持单行
          const combined = prev + chunk.delta
          const nl = combined.indexOf('\n')
          return nl >= 0 ? combined.slice(0, nl) : combined
        })
      }
      if (chunk.done) {
        cleanup()
      }
    })
    chunkUnsubRef.current = unsubscribe

    const systemPrompt = [
      '你是一位专业中文写作助手，擅长根据上文给出自然的续写建议。',
      bookTitle ? `当前作品：《${bookTitle}》` : '',
      chapterTitle ? `当前章节：${chapterTitle}` : '',
      '【续写要求】',
      '- 只输出紧接在光标处的续写片段，**不要重复上文、不要加标题、不要加引号或任何解释**。',
      '- 长度控制在 1 句话以内（20~40 字最佳），严禁跨段落。',
      '- 严格保持与上文一致的人称、时态、语气、用词风格。',
      '- 若上文是对话中，续写可以补完本句；若上文刚结尾，可给一个自然的后续短句。',
    ]
      .filter(Boolean)
      .join('\n')

    const userPrompt = `【上文】\n${trigger.prefix}\n\n【续写紧接上文】：`

    window.electronAPI.aiChatStream({
      apiKey: model.apiKey,
      baseURL: model.baseUrl || undefined,
      apiProvider: model.apiProvider === 'anthropic' ? 'anthropic' : 'openai',
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      options: {
        model: model.name,
        temperature: 0.5,
        thinking: { type: 'disabled' },
        max_tokens: 120,
      },
      tools: [],
      useToolRouter: false,
      bookId: bookId ?? undefined,
      chapterId: chapterId ?? undefined,
      currentChapterTitle: chapterTitle || undefined,
      writingChapters: [],
      availableOutlines: [],
      agentMode: 'legacy',
      chatAgentMode: 'ask',
    })

    // 组件卸载或 trigger 变化时清理
    return () => {
      cleanup()
    }
  }, [trigger, model, bookId, chapterId, chapterTitle, bookTitle, cleanup, clearGhost])

  // 键盘接管：Tab 接受，Esc 取消（只在有 ghost 时生效）
  React.useEffect(() => {
    if (!ghostText) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Tab') {
        e.preventDefault()
        e.stopPropagation()
        const text = ghostTextRef.current
        clearGhost()
        onAccept(text)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        e.stopPropagation()
        clearGhost()
        onCancel()
      }
    }
    // capture 阶段：抢在 Lexical Tab 处理之前
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [ghostText, clearGhost, onAccept, onCancel])

  if (!ghostText || !rect) return null

  const style: React.CSSProperties = {
    position: 'fixed',
    top: rect.top,
    left: rect.right,
    height: rect.height,
    lineHeight: `${rect.height}px`,
  }

  return (
    <div className="ghost-completion" style={style} aria-hidden>
      <span className="ghost-completion-text">{ghostText}</span>
      <span className="ghost-completion-hint">Tab 接受 · Esc 取消</span>
    </div>
  )
}
