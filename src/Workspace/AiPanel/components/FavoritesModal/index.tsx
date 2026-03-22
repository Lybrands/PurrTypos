import React from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button, Empty, List, Modal, Spin, Tooltip } from 'antd'
import { DeleteOutlined } from '@ant-design/icons'
import type { AiFavorite } from '../../../../types'
import { formatFavoriteTime } from './formatFavoriteTime'
import './index.scss'

export interface FavoritesModalProps {
  open: boolean
  onCancel: () => void
}

export default function FavoritesModal({ open, onCancel }: FavoritesModalProps) {
  const [list, setList] = React.useState<AiFavorite[]>([])
  const [loading, setLoading] = React.useState(false)
  const [detail, setDetail] = React.useState<AiFavorite | null>(null)

  React.useEffect(() => {
    if (!open) return
    setLoading(true)
    setDetail(null)
    window.electronAPI.getAiFavorites().then((res) => {
      setLoading(false)
      if (res.success && res.data) setList(res.data)
      else setList([])
    })
  }, [open])

  const handleDelete = React.useCallback(async (id: number) => {
    const res = await window.electronAPI.deleteAiFavorite({ id })
    if (res.success) setList((prev) => prev.filter((f) => f.id !== id))
  }, [])

  return (
    <>
      <Modal
        title="收藏列表"
        open={open}
        onCancel={onCancel}
        footer={null}
        width={560}
        destroyOnHidden
        className="ai-favorites-modal"
        styles={{ body: { maxHeight: '60vh' } }}
      >
        {loading ? (
          <div className="favorites-modal-loading"><Spin /></div>
        ) : list.length === 0 ? (
          <Empty image={false} description="暂无收藏" />
        ) : (
          <List
            className="favorites-list"
            dataSource={list}
            renderItem={(item) => {
              const promptText = (item.prompt ?? '').trim()
              const answerText = (item.content ?? '').trim()
              const preview = promptText
                ? `问：${promptText} 答：${answerText}`
                : answerText ? `答：${answerText}` : '—'
              return (
                <List.Item className="favorites-list-item">
                  <div
                    className="favorites-item-main"
                    role="button"
                    tabIndex={0}
                    onClick={() => setDetail(item)}
                    onKeyDown={(e) => e.key === 'Enter' && setDetail(item)}
                  >
                    <div className="favorites-item-head">
                      <span className="favorites-item-title">{item.session_title}</span>
                      <span className="favorites-item-time">{formatFavoriteTime(item.create_time)}</span>
                      <Tooltip title="删除">
                        <Button
                          type="text"
                          size="small"
                          icon={<DeleteOutlined />}
                          className="favorites-item-delete"
                          onClick={(e) => { e.stopPropagation(); handleDelete(item.id) }}
                        />
                      </Tooltip>
                    </div>
                    <div className="favorites-item-preview">{preview || '—'}</div>
                  </div>
                </List.Item>
              )
            }}
          />
        )}
      </Modal>

      <Modal
        title="收藏详情"
        open={detail != null}
        onCancel={() => setDetail(null)}
        footer={null}
        width={560}
        destroyOnHidden
        className="ai-favorite-detail-modal"
        styles={{ body: { maxHeight: '70vh' } }}
      >
        {detail && (
          <div className="favorite-detail-body">
            <div className="favorite-detail-section">
              <div className="favorite-detail-label">问</div>
              <div className="favorite-detail-text">{(detail.prompt ?? '').trim() || '（未记录提问）'}</div>
            </div>
            <div className="favorite-detail-section">
              <div className="favorite-detail-label">答</div>
              <div className="favorite-detail-text favorite-detail-answer-md">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.content || '—'}</ReactMarkdown>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </>
  )
}
