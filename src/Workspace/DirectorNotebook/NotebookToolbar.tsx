import React from 'react'
import { Button, Modal, Tag, Tooltip } from 'antd'
import {
  TeamOutlined,
  GlobalOutlined,
  BulbOutlined,
  HighlightOutlined,
} from '@ant-design/icons'
import { useWorkspace } from '../WorkspaceContext'
import StyleForm, { type StyleFormStatus } from './StyleForm'
import './NotebookToolbar.scss'

type ToolKey = 'characters' | 'background' | 'memory' | 'style'

interface ToolDef {
  key: ToolKey
  icon: React.ReactNode
  label: string
}

const TOOLS: ToolDef[] = [
  { key: 'characters', icon: <TeamOutlined />, label: '人物' },
  { key: 'background', icon: <GlobalOutlined />, label: '故事背景' },
  { key: 'memory', icon: <BulbOutlined />, label: '记忆 / 伏笔' },
  { key: 'style', icon: <HighlightOutlined />, label: '风格基调' },
]

/**
 * 导演笔记本工具栏：把人物 / 故事背景 / 记忆-伏笔 / 风格基调
 * 收成 4 个图标按钮，点击通过弹窗承载。这样左栏只剩章节列表，更纯净。
 */
export default function NotebookToolbar() {
  const { bookId } = useWorkspace()
  const [open, setOpen] = React.useState<ToolKey | null>(null)
  const [styleStatus, setStyleStatus] = React.useState<StyleFormStatus>({ hasRemote: false, saving: false })

  const handleOpen = React.useCallback((key: ToolKey) => () => {
    if (key === 'characters') {
      window.dispatchEvent(new CustomEvent('workspace-open-panel', { detail: { panel: 'setting', open: true } }))
      window.dispatchEvent(new CustomEvent('open-setting-panel', { detail: { tab: 'characters' } }))
      return
    }
    if (key === 'background') {
      window.dispatchEvent(new CustomEvent('workspace-open-panel', { detail: { panel: 'setting', open: true } }))
      window.dispatchEvent(new CustomEvent('open-setting-panel', { detail: { tab: 'background' } }))
      return
    }
    setOpen(key)
  }, [])
  const handleClose = React.useCallback(() => setOpen(null), [])

  const styleStatusTag = React.useMemo(() => {
    if (styleStatus.saving) return <Tag color="processing" style={{ margin: 0 }}>保存中…</Tag>
    if (styleStatus.hasRemote) return <Tag color="success" style={{ margin: 0 }}>已配置</Tag>
    return <Tag style={{ margin: 0 }}>未配置</Tag>
  }, [styleStatus])

  const renderModalTitle = (def: ToolDef, suffix?: React.ReactNode) => (
    <span className="notebook-toolbar-modal-title">
      {def.label}
      {suffix && <span className="notebook-toolbar-modal-suffix">{suffix}</span>}
    </span>
  )

  return (
    <>
      <div className="notebook-toolbar">
        {TOOLS.map((t) => (
          <Tooltip key={t.key} title={t.label} placement="bottom">
            <Button
              type="text"
              size="small"
              icon={t.icon}
              onClick={handleOpen(t.key)}
              className="notebook-toolbar-btn"
            />
          </Tooltip>
        ))}
      </div>

      {/* 人物 / 故事背景已迁移至设定浮窗；保留 memory / style 弹窗 */}

      {/* 记忆 / 伏笔（占位） */}
      <Modal
        open={open === 'memory'}
        onCancel={handleClose}
        title={renderModalTitle(TOOLS[2])}
        footer={null}
        width={520}
        destroyOnClose
        className="notebook-toolbar-modal"
        styles={{ body: { padding: 0 } }}
      >
        <div className="notebook-toolbar-modal-body notebook-toolbar-modal-body--placeholder">
          <p className="notebook-toolbar-placeholder-title">敬请期待</p>
          <p className="notebook-toolbar-placeholder-desc">
            此区域将记录长期记忆、伏笔、世界设定的关键点，作为 AI 的长期上下文。
            <br />
            目前可在 AI 面板里使用「记忆」功能。
          </p>
        </div>
      </Modal>

      {/* 风格基调 */}
      <Modal
        open={open === 'style'}
        onCancel={handleClose}
        title={renderModalTitle(TOOLS[3], styleStatusTag)}
        footer={null}
        width={680}
        destroyOnClose
        className="notebook-toolbar-modal"
        styles={{ body: { padding: 0 } }}
      >
        <div className="notebook-toolbar-modal-body">
          <StyleForm onStatusChange={setStyleStatus} />
        </div>
      </Modal>
    </>
  )
}
