import React from 'react'
import { HistoryOutlined } from '@ant-design/icons'
import { App as AntdApp, Drawer, Empty, Modal, Spin } from 'antd'
import type { EntityId } from '../../types'
import {
  getSettingHistoryAdapter,
  type HistoryKind,
  type HistoryStorageKey,
  type HistoryViewModel,
} from './SettingHistoryAdapter'
import SettingHistoryItem from './SettingHistoryItem'
import './SettingHistoryDrawer.scss'

interface SettingHistoryDrawerProps {
  kind: HistoryKind
  characterId?: number | null
  /** 世界设定实体 ID（kind='entity' 时必传） */
  settingEntityId?: number | null
  bookId?: EntityId | null
  entityTitle?: string
  open: boolean
  onClose: () => void
  onRestored?: () => void
}

type HistoryBuckets<T> = Record<HistoryStorageKey, T>

export default function SettingHistoryDrawer({
  kind,
  characterId,
  settingEntityId,
  bookId,
  entityTitle,
  open,
  onClose,
  onRestored,
}: SettingHistoryDrawerProps) {
  const { message: appMessage } = AntdApp.useApp()
  const [modal, modalContext] = Modal.useModal()
  const adapter = getSettingHistoryAdapter(kind)
  const [loading, setLoading] = React.useState(false)
  const [itemsByStorage, setItemsByStorage] = React.useState<HistoryBuckets<HistoryViewModel[]>>({
    profile: [],
    background: [],
  })
  const [expandedId, setExpandedId] = React.useState<number | null>(null)
  const [detailLoadingId, setDetailLoadingId] = React.useState<number | null>(null)
  const [detailByStorage, setDetailByStorage] = React.useState<HistoryBuckets<HistoryViewModel | null>>({
    profile: null,
    background: null,
  })
  const [restoringId, setRestoringId] = React.useState<number | null>(null)

  const reload = React.useCallback(async () => {
    setLoading(true)
    try {
      const result = await adapter.list({ characterId, settingEntityId, bookId })
      if (result === null) return
      if (result.success) {
        setItemsByStorage((previousItems) => ({
          ...previousItems,
          [adapter.storageKey]: result.data,
        }))
      } else {
        appMessage.error(adapter.loadErrorMessage)
      }
    } catch {
      appMessage.error('加载历史失败')
    } finally {
      setLoading(false)
    }
  }, [adapter, appMessage, bookId, characterId, settingEntityId])

  React.useEffect(() => {
    if (open) {
      setExpandedId(null)
      setDetailByStorage({ profile: null, background: null })
      void reload()
    }
  }, [open, reload])

  const loadDetail = async (historyId: number) => {
    setDetailLoadingId(historyId)
    try {
      const detail = await adapter.getDetail(historyId)
      if (detail) {
        setDetailByStorage((previousDetails) => ({
          ...previousDetails,
          [adapter.storageKey]: detail,
        }))
      }
    } finally {
      setDetailLoadingId(null)
    }
  }

  const handleRestore = (historyId: number, preview: string) => {
    modal.confirm({
      title: '回退到此版本',
      content: (
        <div>
          <p>当前设定将被替换为这条历史的「之前」状态：</p>
          <pre className="setting-history-restore-preview">{preview.slice(0, 400)}{preview.length > 400 ? '…' : ''}</pre>
        </div>
      ),
      okText: '回退',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        setRestoringId(historyId)
        try {
          const result = await adapter.rollback(historyId)
          if (result.success) {
            appMessage.success('已回退到该版本')
            onRestored?.()
            void reload()
          } else {
            appMessage.error(result.error || '回退失败')
          }
        } finally {
          setRestoringId(null)
        }
      },
    })
  }

  const items = itemsByStorage[adapter.storageKey]
  const detail = detailByStorage[adapter.storageKey]

  return (
    <Drawer
      title={(
        <span>
          <HistoryOutlined style={{ marginRight: 8 }} />
          {adapter.title}
          {entityTitle ? (
            <span style={{ color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
              · {entityTitle}
            </span>
          ) : null}
        </span>
      )}
      placement="right"
      width={560}
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      {modalContext}
      {loading ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
      ) : items.length === 0 ? (
        <Empty description="还没有修改历史" />
      ) : (
        <div className="setting-history-list">
          {items.map((item) => {
            const isExpanded = expandedId === item.id
            return (
              <SettingHistoryItem
                key={item.id}
                item={item}
                expanded={isExpanded}
                detail={detail}
                detailLoading={detailLoadingId === item.id}
                restoring={restoringId === item.id}
                restoreTooltip={adapter.restoreTooltip}
                onToggle={() => {
                  if (isExpanded) {
                    setExpandedId(null)
                  } else {
                    setExpandedId(item.id)
                    void loadDetail(item.id)
                  }
                }}
                onRestore={() => handleRestore(item.id, item.beforeContent || '')}
              />
            )
          })}
        </div>
      )}
    </Drawer>
  )
}
