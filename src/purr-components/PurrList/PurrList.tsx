import React from 'react'
import '../styles/purr.scss'

export interface PurrListProps<T> {
  dataSource?: T[]
  renderItem?: (item: T, index: number) => React.ReactNode
  loading?: boolean
  locale?: { emptyText?: React.ReactNode }
  children?: React.ReactNode
  className?: string
  size?: 'small' | 'default' | 'large'
}

export interface PurrListItemProps extends React.HTMLAttributes<HTMLDivElement> {
  actions?: React.ReactNode[]
}

export interface PurrListItemMetaProps {
  title?: React.ReactNode
  description?: React.ReactNode
}

function PurrListBase<T>({
  dataSource,
  renderItem,
  loading,
  locale,
  children,
  className,
}: PurrListProps<T>) {
  return (
    <div className={['purr-list', className].filter(Boolean).join(' ')}>
      {loading
        ? <div className="purr-list__empty">加载中…</div>
        : dataSource?.length
          ? dataSource.map((item, index) => (
            <React.Fragment key={index}>{renderItem?.(item, index)}</React.Fragment>
          ))
          : children ?? <div className="purr-list__empty">{locale?.emptyText ?? '暂无数据'}</div>}
    </div>
  )
}

function PurrListItem({ children, actions, className, ...props }: PurrListItemProps) {
  return (
    <div {...props} className={['purr-list__item', className].filter(Boolean).join(' ')}>
      <div className="purr-list__item-content">{children}</div>
      {actions?.length
        ? <div className="purr-list__actions">{actions.map((action, index) => <div key={index}>{action}</div>)}</div>
        : null}
    </div>
  )
}

function PurrListItemMeta({ title, description }: PurrListItemMetaProps) {
  return (
    <div className="purr-list__meta">
      {title != null && <div className="purr-list__meta-title">{title}</div>}
      {description != null && <div className="purr-list__meta-description">{description}</div>}
    </div>
  )
}

type PurrListComponent = typeof PurrListBase & {
  Item: typeof PurrListItem & { Meta: typeof PurrListItemMeta }
}

export const PurrList = PurrListBase as PurrListComponent
PurrList.Item = PurrListItem as typeof PurrListItem & { Meta: typeof PurrListItemMeta }
PurrList.Item.Meta = PurrListItemMeta
