import React from 'react'
import { PurrButton } from '../PurrButton'
import { PurrDialog } from '../PurrDialog'

export type PurrConfirmResult = 'confirm' | 'cancel' | string

export interface PurrConfirmAction {
  id: string
  label: React.ReactNode
  variant?: 'default' | 'primary' | 'danger' | 'text' | 'link'
}

export interface PurrConfirmOptions {
  title: React.ReactNode
  content: React.ReactNode
  confirmText?: React.ReactNode
  confirmVariant?: 'primary' | 'danger'
  cancelText?: React.ReactNode
  actions?: PurrConfirmAction[]
  zIndex?: number
}

export type PurrConfirmApi = (options: PurrConfirmOptions) => Promise<PurrConfirmResult>

const ConfirmContext = React.createContext<PurrConfirmApi | null>(null)

interface PendingConfirm {
  options: PurrConfirmOptions
  resolve: (result: PurrConfirmResult) => void
}

/** 提供 Promise 风格的确认框，供需要分支选择的业务逻辑使用。 */
export function PurrConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = React.useState<PendingConfirm | null>(null)
  const pendingRef = React.useRef<PendingConfirm | null>(null)

  const confirm = React.useCallback<PurrConfirmApi>((options) => new Promise((resolve) => {
    pendingRef.current?.resolve('cancel')
    const next = { options, resolve }
    pendingRef.current = next
    setPending(next)
  }), [])

  const finish = React.useCallback((result: PurrConfirmResult) => {
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
        <PurrDialog
          zIndex={options.zIndex}
          open
          title={options.title}
          onOpenChange={(open) => { if (!open) finish('cancel') }}
          footer={(
            <>
              <PurrButton onClick={() => finish('cancel')}>{options.cancelText ?? '取消'}</PurrButton>
              {options.actions?.map((action) => (
                <PurrButton key={action.id} variant={action.variant} onClick={() => finish(action.id)}>{action.label}</PurrButton>
              ))}
              <PurrButton variant={options.confirmVariant ?? 'primary'} onClick={() => finish('confirm')}>
                {options.confirmText ?? '确认'}
              </PurrButton>
            </>
          )}
        >
          {options.content}
        </PurrDialog>
      )}
    </ConfirmContext.Provider>
  )
}

export function usePurrConfirm(): PurrConfirmApi {
  const confirm = React.useContext(ConfirmContext)
  if (!confirm) throw new Error('usePurrConfirm 必须在 PurrConfirmProvider 内使用')
  return confirm
}
