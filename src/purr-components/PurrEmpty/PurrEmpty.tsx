import React from 'react'
import '../styles/purr.scss'

export interface PurrEmptyProps {
  description?: React.ReactNode
  image?: React.ReactNode | false
  className?: string
  children?: React.ReactNode
}

export function PurrEmpty({ description = '暂无数据', image, className, children }: PurrEmptyProps) {
  return (
    <div className={['purr-empty', className].filter(Boolean).join(' ')}>
      {image !== false && <div className="purr-empty__icon" aria-hidden>{image ?? '◇'}</div>}
      {description !== false && <div className="purr-empty__description">{description}</div>}
      {children && <div className="purr-empty__footer">{children}</div>}
    </div>
  )
}

export namespace PurrEmpty {
  export const PRESENTED_IMAGE_SIMPLE = '◇'
}
