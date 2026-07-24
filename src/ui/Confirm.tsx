import React from 'react'
import { Button } from './Button'
import { Dialog } from './Dialog/Dialog'

export type ConfirmResult = 'confirm' | 'cancel' | string

export interface ConfirmAction {
  id: string
  label: React.ReactNode
  variant?: 'default' | 'primary' | 'danger' | 'text' | 'link'
}

export interface ConfirmOptions {
  title: React.ReactNode
  content: React.ReactNode
  confirmText?: React.ReactNode
  confirmVariant?: 'primary' | 'danger'
  cancelText?: React.ReactNode
  actions?: ConfirmAction[]
}

export type ConfirmApi = (options: ConfirmOptions) => Promise<ConfirmResult>

const ConfirmContext = React.createContext<ConfirmApi | null>(null)

interface PendingConfirm {
  options: ConfirmOptions
  resolve: (result: ConfirmResult) => void
}

/** 提供 Promise 风格的确认框，供需要分支选择的业务逻辑使用。 */
export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = React.useState<PendingConfirm | null>(null)
  const pendingRef = React.useRef<PendingConfirm | null>(null)

  const confirm = React.useCallback<ConfirmApi>((options) => new Promise((resolve) => {
    pendingRef.current?.resolve('cancel')
    const next = { options, resolve }
    pendingRef.current = next
    setPending(next)
  }), [])

  const finish = React.useCallback((result: ConfirmResult) => {
    const current = pendingRef.current
    if (!current) return
    pendingRef.current = null
    setPending(null)
    current.resolve(result)
  }, [])

  React.useEffect(() => () => {
    pendingRef.current?.resolve('cancel')
    pendingRef.current = null
  }, [])

  const options = pending?.options
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {options && (
        <Dialog
          open
          title={options.title}
          onOpenChange={(open) => { if (!open) finish('cancel') }}
          footer={(
            <>
              <Button onClick={() => finish('cancel')}>{options.cancelText ?? '取消'}</Button>
              {options.actions?.map((action) => (
                <Button key={action.id} variant={action.variant} onClick={() => finish(action.id)}>{action.label}</Button>
              ))}
              <Button variant={options.confirmVariant ?? 'primary'} onClick={() => finish('confirm')}>
                {options.confirmText ?? '确认'}
              </Button>
            </>
          )}
        >
          {options.content}
        </Dialog>
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): ConfirmApi {
  const confirm = React.useContext(ConfirmContext)
  if (!confirm) throw new Error('useConfirm 必须在 ConfirmProvider 内使用')
  return confirm
}
