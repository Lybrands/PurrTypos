import React from 'react'
import { getOverlayLayerStyle } from '../overlayLayer'
import '../styles/purr.scss'

export type PurrToastLevel = 'success' | 'error' | 'warning' | 'info'

export interface PurrToastMessage {
  key: number
  level: PurrToastLevel
  content: React.ReactNode
}

export interface PurrToastApi {
  success: (content: React.ReactNode) => void
  error: (content: React.ReactNode) => void
  warning: (content: React.ReactNode) => void
  info: (content: React.ReactNode) => void
}

let publish: ((level: PurrToastLevel, content: React.ReactNode) => void) | null = null

function send(level: PurrToastLevel, content: React.ReactNode) {
  publish?.(level, content)
}

/** 可在组件和非 React 模块中使用的应用反馈 API。 */
export const purrToast: PurrToastApi = {
  success: (content) => send('success', content),
  error: (content) => send('error', content),
  warning: (content) => send('warning', content),
  info: (content) => send('info', content),
}

export function usePurrToast(): PurrToastApi {
  return purrToast
}

export interface PurrToastProviderProps {
  children: React.ReactNode
  zIndex?: number
}

export function PurrToastProvider({ children, zIndex }: PurrToastProviderProps) {
  const [messages, setMessages] = React.useState<PurrToastMessage[]>([])
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

  const levelIcon: Record<PurrToastLevel, string> = {
    success: '✓',
    error: '!',
    warning: '!',
    info: 'i',
  }

  return (
    <>
      {children}
      <div
        className="purr-toast-region"
        style={getOverlayLayerStyle('PurrToastProvider', zIndex)}
        aria-live="polite"
        aria-atomic="true"
      >
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
