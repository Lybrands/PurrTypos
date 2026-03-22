import React from 'react'
import { EditOutlined, ImportOutlined, PaperClipOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Modal, Popconfirm, Space, Tooltip, Typography } from 'antd'
import MarkdownWithSearch from '../search/MarkdownWithSearch'
import { useWorkspace } from '../WorkspaceContext'
import type { Editor } from '@tiptap/core'
import { Extension, mergeAttributes } from '@tiptap/core'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Heading from '@tiptap/extension-heading'
import { TableKit } from '@tiptap/extension-table'
import type { StoryBackgroundAttachment } from '../../types'
import { getStoryBackground } from '../utils'
import { useAntdApp } from '../../hooks/useAntdApp'
import { markdownToHtml, htmlToMarkdown } from '../../utils/markdown'
import './StoryBackgroundTab.scss'

/** 悬停标题时显示原生 tooltip：第几级标题（与 StarterKit 默认 heading 二选一） */
const storyBackgroundHeading = Heading.extend({
  renderHTML({ node, HTMLAttributes }) {
    const hasLevel = this.options.levels.includes(node.attrs.level)
    const level = hasLevel ? node.attrs.level : this.options.levels[0]
    return [
      `h${level}`,
      mergeAttributes(this.options.HTMLAttributes, HTMLAttributes, {
        title: `第 ${level} 级标题`,
        'data-heading-level': String(level),
      }),
      0,
    ]
  },
}).configure({ levels: [1, 2, 3, 4] })

/** Tab 键：列表内缩进，非列表插入制表符；始终阻止失焦 */
const LiteralTab = Extension.create({
  name: 'literalTab',
  addKeyboardShortcuts() {
    return {
      Tab: () => {
        if (this.editor.commands.sinkListItem('listItem')) return true
        this.editor.commands.insertContent('\t')
        return true
      },
    }
  },
})

interface StoryBackgroundTabProps {
  bookId: number | null
}

export default function StoryBackgroundTab({ bookId }: StoryBackgroundTabProps) {
  const { message } = useAntdApp()
  const { workspaceSearchQuery, notifyWorkspaceSearchContentChanged } = useWorkspace()
  const [content, setContent] = React.useState('')
  const [attachments, setAttachments] = React.useState<StoryBackgroundAttachment[]>([])
  const [editing, setEditing] = React.useState(false)
  const [loading, setLoading] = React.useState(false)
  const [attachmentModalOpen, setAttachmentModalOpen] = React.useState(false)
  const initialDraftRef = React.useRef('')

  const editor = useEditor({
    immediatelyRender: true,
    extensions: [
      LiteralTab,
      StarterKit.configure({
        heading: false,
      }),
      storyBackgroundHeading,
      TableKit,
    ],
    content: '<p></p>',
    editorProps: {
      attributes: {
        class: 'story-background-tiptap-editable',
        spellcheck: 'false',
      },
      handlePaste: (view, event) => {
        const text = event.clipboardData?.getData('text/plain') ?? ''
        if (!text.trim()) return false
        const looksLikeMarkdown = /^#+\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+|\n\s*[-*+]\s|\n#+\s|^>\s|^\s*\|.+\|/m.test(text)
        if (looksLikeMarkdown) {
          event.preventDefault()
          const html = markdownToHtml(text)
          editorRef.current?.commands.insertContent(html)
          return true
        }
        return false
      },
    },
  }, [editing])

  const editorRef = React.useRef<Editor | null>(null)
  React.useEffect(() => {
    editorRef.current = editor ?? null
  }, [editor])

  React.useEffect(() => {
    if (editing && editor) {
      editor.commands.setContent(markdownToHtml(initialDraftRef.current))
    }
  }, [editing, editor])

  const loadContent = React.useCallback(async () => {
    if (bookId == null) return
    setLoading(true)
    try {
      const [bg, attRes] = await Promise.all([
        getStoryBackground(bookId),
        window.electronAPI.getStoryBackgroundAttachments({ bookId }),
      ])
      setContent(bg?.content ?? '')
      setAttachments(attRes.success && Array.isArray(attRes.data) ? attRes.data : [])
    } finally {
      setLoading(false)
    }
  }, [bookId])

  React.useEffect(() => {
    loadContent()
  }, [loadContent])

  React.useEffect(() => {
    notifyWorkspaceSearchContentChanged()
  }, [content, notifyWorkspaceSearchContentChanged])

  const handleAdd = React.useCallback(() => {
    initialDraftRef.current = content
    setEditing(true)
  }, [content])

  const handleSave = React.useCallback(async () => {
    if (bookId == null) return
    const ed = editorRef.current
    const md = ed ? htmlToMarkdown(ed.getHTML()) : ''
    setLoading(true)
    try {
      const res = await window.electronAPI.saveStoryBackground({ bookId, content: md })
      if (res.success) {
        setContent(md)
        setEditing(false)
        message.success('已保存')
      } else {
        message.error(res.error || '保存失败')
      }
    } finally {
      setLoading(false)
    }
  }, [bookId, message])

  const handleCancel = React.useCallback(() => {
    setEditing(false)
  }, [])

  const handleImportFile = React.useCallback(async () => {
    const res = await window.electronAPI.openAndReadTextFile()
    if (res.success && res.data != null) {
      const ed = editorRef.current
      if (ed) {
        const currentMd = htmlToMarkdown(ed.getHTML())
        const appended = currentMd.trim() ? `${currentMd}\n\n${res.data}` : res.data
        ed.commands.setContent(markdownToHtml(appended))
      }
      message.success('已追加导入内容')
    } else if (res.error !== 'canceled') {
      message.error(res.error || '读取文件失败')
    }
  }, [message])

  const handlePickAttachments = React.useCallback(async () => {
    if (bookId == null) return
    try {
      const res = await window.electronAPI.pickStoryBackgroundAttachments({ bookId })
      if (res.success) {
        const count = (res as { addedCount?: number }).addedCount ?? res.data?.length ?? 0
        if (count > 0) message.success(`已添加 ${count} 个附件`)
        if (Array.isArray(res.data)) setAttachments(res.data)
      } else if (res.error !== 'canceled') {
        message.error(res.error || '导入附件失败')
        const listRes = await window.electronAPI.getStoryBackgroundAttachments({ bookId })
        if (listRes.success && Array.isArray(listRes.data)) setAttachments(listRes.data)
      }
    } catch (_) {
      message.error('导入附件失败')
      const listRes = await window.electronAPI.getStoryBackgroundAttachments({ bookId })
      if (listRes.success && Array.isArray(listRes.data)) setAttachments(listRes.data)
    }
  }, [bookId, message])

  const handleDeleteAttachment = React.useCallback(async (id: number) => {
    const res = await window.electronAPI.deleteStoryBackgroundAttachment({ id })
    if (res.success) {
      setAttachments((prev) => prev.filter((a) => a.id !== id))
      message.success('已删除')
    } else {
      message.error(res.error || '删除失败')
    }
  }, [message])

  const handleOpenAttachment = React.useCallback((storedPath: string) => {
    window.electronAPI.openStoryBackgroundAttachment({ storedPath })
  }, [])

  const handlePickAttachmentsInModal = React.useCallback(async () => {
    await handlePickAttachments()
  }, [handlePickAttachments])

  const attachmentModalContent = (
    <div className="story-background-attachments-modal">
      {attachments.length === 0 ? (
        <div className="story-background-attachments-empty">暂无附件</div>
      ) : (
        <ul className="story-background-attachment-list">
          {attachments.map((a) => (
            <li key={a.id} className="story-background-attachment-item">
              <Space size="small" style={{ width: '100%' }}>
                <PaperClipOutlined />
                <Typography.Link
                  ellipsis
                  onClick={() => handleOpenAttachment(a.stored_path)}
                  style={{ flex: 1, minWidth: 0 }}
                >
                  {a.name}
                </Typography.Link>
                {editing && (
                  <Popconfirm
                    title="确定删除该附件？"
                    onConfirm={() => handleDeleteAttachment(a.id)}
                  >
                    <Button type="text" size="small" danger>
                      删除
                    </Button>
                  </Popconfirm>
                )}
              </Space>
            </li>
          ))}
        </ul>
      )}
    </div>
  )

  if (bookId == null) {
    return (
      <div className="story-background-tab">
        <div className="story-background-empty">
          <p>请先选择书籍</p>
        </div>
      </div>
    )
  }

  if (editing) {
    return (
      <div className="story-background-tab story-background-editing">
        <div className="story-background-editing-header">
          <div className="story-background-toolbar story-background-toolbar-top">
            <Tooltip title="导入文件">
              <Button
                type="text"
                size="small"
                icon={<ImportOutlined />}
                onClick={handleImportFile}
                disabled={loading}
              />
            </Tooltip>
            <Tooltip title={attachments.length > 0 ? `附件 (${attachments.length})` : '附件'}>
              <Button
                type="text"
                size="small"
                icon={<PaperClipOutlined />}
                onClick={() => setAttachmentModalOpen(true)}
              />
            </Tooltip>
          </div>
        </div>
        <div className="story-background-editor-wrap story-background-tiptap-wrap">
          <EditorContent editor={editor} className="story-background-tiptap-container" />
        </div>
        <div className="story-background-toolbar story-background-toolbar-bottom">
          <Button type="primary" size="small" onClick={handleSave} disabled={loading}>
            保存
          </Button>
          <Button size="small" onClick={handleCancel} disabled={loading}>
            取消
          </Button>
        </div>
        <Modal
          title="附件"
          open={attachmentModalOpen}
          onCancel={() => setAttachmentModalOpen(false)}
          footer={
            editing
              ? [
                  <Button key="add" icon={<PaperClipOutlined />} onClick={handlePickAttachmentsInModal}>
                    导入附件
                  </Button>,
                  <Button key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
                    关闭
                  </Button>,
                ]
              : [
                  <Button key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
                    关闭
                  </Button>,
                ]
          }
        >
          {attachmentModalContent}
        </Modal>
      </div>
    )
  }

  if (!content.trim()) {
    return (
      <div className="story-background-tab">
        <div
          className="story-background-empty-card outline-empty-card outline-empty-card-action"
          onClick={handleAdd}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          role="button"
          tabIndex={0}
        >
          <PlusOutlined className="outline-empty-card-icon" />
          <p className="outline-empty-card-title">添加小说背景</p>
          <p className="outline-empty-card-desc">点击此处填写世界观、时代背景、设定等</p>
        </div>
      </div>
    )
  }

  return (
    <div className="story-background-tab">
      <div className="story-background-view">
        <div className="story-background-view-header">
          <div className="story-background-toolbar story-background-toolbar-top">
            <Tooltip title="编辑">
              <Button type="text" size="small" icon={<EditOutlined />} onClick={handleAdd} />
            </Tooltip>
            <Tooltip title={attachments.length > 0 ? `附件 (${attachments.length})` : '附件'}>
              <Button
                type="text"
                size="small"
                icon={<PaperClipOutlined />}
                onClick={() => setAttachmentModalOpen(true)}
              />
            </Tooltip>
          </div>
        </div>
        <div className="story-background-content story-background-markdown">
          <MarkdownWithSearch content={content || ''} searchQuery={workspaceSearchQuery} />
        </div>
        <Modal
          title="附件"
          open={attachmentModalOpen}
          onCancel={() => setAttachmentModalOpen(false)}
          footer={[
            <Button key="close" type="primary" onClick={() => setAttachmentModalOpen(false)}>
              关闭
            </Button>,
          ]}
        >
          {attachmentModalContent}
        </Modal>
      </div>
    </div>
  )
}
