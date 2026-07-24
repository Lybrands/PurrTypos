import React from 'react'
import './ui.scss'

export type ToastLevel = 'success' | 'error' | 'warning' | 'info'

export interface ToastMessage {
  key: number
  level: ToastLevel
  content: React.ReactNode
}

export interface ToastApi {
  success: (content: React.ReactNode) => void
  error: (content: React.ReactNode) => void
  warning: (content: React.ReactNode) => void
  info: (content: React.ReactNode) => void
}

let publish: ((level: ToastLevel, content: React.ReactNode) => void) | null = null

function send(level: ToastLevel, content: React.ReactNode) {
  publish?.(level, content)
}

/** 可在组件和非 React 模块中使用的应用反馈 API。 */
export const toast: ToastApi = {
  success: (content) => send('success', content),
  error: (content) => send('error', content),
  warning: (content) => send('warning', content),
  info: (content) => send('info', content),
}

export function useToast(): ToastApi {
  return toast
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = React.useState<ToastMessage[]>([])
  const nextKey = React.useRef(0)
  const dismiss = React.useCallback((key: number) => {
    setMessages((current) => current.filter((message) => message.key !== key))
  }, [])

  React.useEffect(() => {
    publish = (level, content) => {
      const key = ++nextKey.current
      setMessages((current) => [...current, { key, level, content }])
      window.setTimeout(() => {
        dismiss(key)
      }, level === 'error' ? 5000 : 3000)
    }
    return () => { publish = null }
  }, [dismiss])

  const levelIcon: Record<ToastLevel, string> = {
    success: '✓',
    error: '!',
    warning: '!',
    info: 'i',
  }

  return (
    <>
      {children}
      <div className="purr-toast-region" aria-live="polite" aria-atomic="true">
        {messages.map((message) => (
          <div key={message.key} className={`purr-toast purr-toast--${message.level}`} role={message.level === 'error' ? 'alert' : 'status'}>
            <span className="purr-toast__icon" aria-hidden>{levelIcon[message.level]}</span>
            <span className="purr-toast__content">{message.content}</span>
            <button
              type="button"
              className="purr-toast__close"
              aria-label="关闭通知"
              onClick={() => dismiss(message.key)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </>
  )
}
