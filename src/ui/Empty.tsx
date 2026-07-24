import React from 'react'
import './ui.scss'

export interface EmptyProps {
  description?: React.ReactNode
  image?: React.ReactNode | false
  className?: string
  children?: React.ReactNode
}

export function Empty({ description = '暂无数据', image, className, children }: EmptyProps) {
  return (
    <div className={['purr-empty', className].filter(Boolean).join(' ')}>
      {image !== false && <div className="purr-empty__icon" aria-hidden>{image ?? '◇'}</div>}
      {description !== false && <div className="purr-empty__description">{description}</div>}
      {children && <div className="purr-empty__footer">{children}</div>}
    </div>
  )
}

export namespace Empty {
  export const PRESENTED_IMAGE_SIMPLE = '◇'
}
