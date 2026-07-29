import { services } from '@/services'
import React, { forwardRef, useImperativeHandle } from 'react'
import { EditOutlined, HistoryOutlined, ImportOutlined, UserOutlined } from '../../ui'
import { Button, Empty, Tooltip, useToast } from '../../ui'
import FloatingPanel from '../../components/FloatingPanel'
import MarkdownWithSearch from '../search/MarkdownWithSearch'
import OutlineHistoryDrawer from './OutlineHistoryDrawer'
import { useWorkspace } from '../WorkspaceContext'
import type { Editor } from '@tiptap/core'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { TableKit } from '@tiptap/extension-table'
import { markdownToHtml, htmlToMarkdown } from '../../utils/markdown'
import type { EntityId } from '../../types'
import CharacterTab from './CharacterTab'
import {
  appendImportedMarkdown,
  handleMarkdownPaste,
  LiteralTab,
} from './markdownEditorShared'
import './StoryBackgroundTab.scss'
import './OutlineMarkdownPane.scss'

export interface OutlineMarkdownPaneRef {
  flushSave: () => Promise<void>
}

interface OutlineMarkdownPaneProps {
  outlineId: EntityId
  markdownContent: string | null
  onSaved: () => void
}

const OutlineMarkdownPane = forwardRef<OutlineMarkdownPaneRef, OutlineMarkdownPaneProps>(
  function OutlineMarkdownPane({ outlineId, markdownContent, onSaved }, ref) {
    const appMessage = useToast()
    const { bookId, workspaceSearchQuery, notifyWorkspaceSearchContentChanged } = useWorkspace()

    React.useEffect(() => {
      notifyWorkspaceSearchContentChanged()
    }, [markdownContent, notifyWorkspaceSearchContentChanged])
    const [editing, setEditing] = React.useState(false)
    const [historyOpen, setHistoryOpen] = React.useState(false)
    const editingRef = React.useRef(false)
    React.useEffect(() => {
      editingRef.current = editing
    }, [editing])

    const initialDraftRef = React.useRef('')
    const baselineSavedRef = React.useRef(markdownContent ?? '')
    React.useEffect(() => {
      baselineSavedRef.current = markdownContent ?? ''
    }, [markdownContent])

    const editorRef = React.useRef<Editor | null>(null)

    const editor = useEditor({
      immediatelyRender: true,
      extensions: [
        LiteralTab,
        StarterKit.configure({
          heading: { levels: [1, 2, 3, 4] },
        }),
        TableKit,
      ],
      content: '<p></p>',
      editorProps: {
        attributes: {
          class: 'story-background-tiptap-editable',
          spellcheck: 'false',
        },
        handlePaste: (_view, event) => handleMarkdownPaste(editorRef.current, event),
      },
    }, [editing])

    const [editorIsEmpty, setEditorIsEmpty] = React.useState(true)
    const [characterPanelOpen, setCharacterPanelOpen] = React.useState(false)
    const [characterPanelInitialPos, setCharacterPanelInitialPos] = React.useState<{ x: number; y: number } | undefined>(undefined)
    const [characterPanelPinned, setCharacterPanelPinned] = React.useState(false)
    const pinStateBeforeActionRef = React.useRef<boolean | null>(null)

    React.useEffect(() => {
      if (!characterPanelOpen) {
        pinStateBeforeActionRef.current = null
      }
    }, [characterPanelOpen])

    const handleCharacterActionActiveChange = React.useCallback((active: boolean) => {
      if (active) {
        if (pinStateBeforeActionRef.current == null) {
          pinStateBeforeActionRef.current = characterPanelPinned
          if (!characterPanelPinned) setCharacterPanelPinned(true)
        }
        return
      }
      if (pinStateBeforeActionRef.current != null) {
        setCharacterPanelPinned(pinStateBeforeActionRef.current)
        pinStateBeforeActionRef.current = null
      }
    }, [characterPanelPinned])

    React.useEffect(() => {
      editorRef.current = editor ?? null
    }, [editor])

    React.useEffect(() => {
      if (editing && editor) {
        editor.commands.setContent(markdownToHtml(initialDraftRef.current))
      }
    }, [editing, editor])

    React.useEffect(() => {
      if (!editor) return
      const syncEmpty = () => setEditorIsEmpty(editor.isEmpty)
      syncEmpty()
      editor.on('update', syncEmpty)
      editor.on('transaction', syncEmpty)
      return () => {
        editor.off('update', syncEmpty)
        editor.off('transaction', syncEmpty)
      }
    }, [editor])

    useImperativeHandle(
      ref,
      () => ({
        async flushSave() {
          if (!editingRef.current || !editorRef.current) return
          const md = htmlToMarkdown(editorRef.current.getHTML())
          if (md === baselineSavedRef.current) return
          const res = await services.outlines.updateOutline({
            outlineId,
            markdown_content: md,
          })
          if (res.success) {
            baselineSavedRef.current = md
            onSaved()
            setEditing(false)
          }
        },
      }),
      [outlineId, onSaved]
    )

    const handleEdit = () => {
      initialDraftRef.current = markdownContent ?? ''
      setEditing(true)
    }

    const handleSave = React.useCallback(async () => {
      const ed = editorRef.current
      if (!ed) return
      const md = htmlToMarkdown(ed.getHTML())
      const res = await services.outlines.updateOutline({
        outlineId,
        markdown_content: md,
      })
      if (res.success) {
        baselineSavedRef.current = md
        appMessage.success('已保存')
        setEditing(false)
        onSaved()
      } else {
        appMessage.error(res.error || '保存失败')
      }
    }, [outlineId, onSaved, appMessage])

    const handleCancel = () => {
      setEditing(false)
    }

    const handleImportFile = React.useCallback(async () => {
      const res = await services.files.openAndReadTextFile()
      if (res.success && res.data != null) {
        const ed = editorRef.current
        if (ed) {
          const currentMd = htmlToMarkdown(ed.getHTML())
          const appended = appendImportedMarkdown(currentMd, res.data)
          ed.commands.setContent(markdownToHtml(appended))
        }
        appMessage.success('已追加导入内容')
      } else if (res.error !== 'canceled') {
        appMessage.error(res.error || '读取文件失败')
      }
    }, [appMessage])

    const content = markdownContent ?? ''

    if (!editing) {
      if (!content.trim()) {
        return (
          <div className="outline-markdown-pane outline-markdown-pane--empty">
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              className="outline-markdown-empty"
              description={
                <div className="outline-markdown-empty-text">
                  <p className="outline-markdown-empty-title">暂无 Markdown 大纲</p>
                  <p className="outline-markdown-empty-desc">
                    可在此整理大纲。
                  </p>
                </div>
              }
            >
              <Button type="primary" size="small" onClick={handleEdit}>
                开始编写
              </Button>
            </Empty>
          </div>
        )
      }
      return (
        <div className="outline-markdown-pane outline-markdown-pane--view story-background-view">
          <div className="story-background-view-header">
            <div className="story-background-toolbar story-background-toolbar-top">
              <Tooltip title="人物">
                <Button
                  type="text"
                  size="small"
                  icon={<UserOutlined />}
                  onClick={(e) => {
                    if (!characterPanelOpen) {
                      const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                      setCharacterPanelInitialPos({
                        x: rect.right + 10,
                        y: Math.max(rect.top - 8, 8),
                      })
                    }
                    setCharacterPanelOpen((v) => !v)
                  }}
                />
              </Tooltip>
              <Tooltip title="历史">
                <Button
                  type="text"
                  size="small"
                  icon={<HistoryOutlined />}
                  onClick={() => setHistoryOpen(true)}
                />
              </Tooltip>
              <Tooltip title="编辑">
                <Button type="text" size="small" icon={<EditOutlined />} onClick={handleEdit} />
              </Tooltip>
            </div>
          </div>
          <div className="story-background-content story-background-markdown">
            <MarkdownWithSearch content={content} searchQuery={workspaceSearchQuery} />
          </div>
          {characterPanelOpen && (
            <FloatingPanel
              title="人物列表"
              width={400}
              open={characterPanelOpen}
              onClose={() => setCharacterPanelOpen(false)}
              initialPinned={false}
              initialPosition={characterPanelInitialPos}
              pinned={characterPanelPinned}
              onPinnedChange={setCharacterPanelPinned}
              storageKey="outline-markdown-character-panel"
            >
              <div className="outline-character-panel-body">
                <CharacterTab bookId={bookId} hideHeader onActionActiveChange={handleCharacterActionActiveChange} />
              </div>
            </FloatingPanel>
          )}
          <OutlineHistoryDrawer
            outlineId={outlineId}
            open={historyOpen}
            onClose={() => setHistoryOpen(false)}
            onRestored={onSaved}
          />
        </div>
      )
    }

    return (
      <div className="outline-markdown-pane outline-markdown-pane--editing story-background-editing">
        <div className="story-background-editing-header">
          <div className="story-background-toolbar story-background-toolbar-top">
            <Tooltip title="人物">
              <Button
                type="text"
                size="small"
                icon={<UserOutlined />}
                onClick={(e) => {
                  if (!characterPanelOpen) {
                    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
                    setCharacterPanelInitialPos({
                      x: rect.right + 10,
                      y: Math.max(rect.top - 8, 8),
                    })
                  }
                  setCharacterPanelOpen((v) => !v)
                }}
              />
            </Tooltip>
            <Tooltip title="历史">
              <Button
                type="text"
                size="small"
                icon={<HistoryOutlined />}
                onClick={() => setHistoryOpen(true)}
              />
            </Tooltip>
            <Tooltip title="导入文件">
              <Button type="text" size="small" icon={<ImportOutlined />} onClick={handleImportFile} />
            </Tooltip>
          </div>
        </div>
        <div className="story-background-editor-wrap story-background-tiptap-wrap">
          <EditorContent editor={editor} className="story-background-tiptap-container" />
        </div>
        <div className="story-background-toolbar story-background-toolbar-bottom">
          <Button type="primary" size="small" onClick={handleSave}>
            保存
          </Button>
          <Button size="small" onClick={handleCancel}>
            取消
          </Button>
        </div>
        {characterPanelOpen && (
          <FloatingPanel
            title="人物列表"
            width={400}
            open={characterPanelOpen}
            onClose={() => setCharacterPanelOpen(false)}
            initialPinned={false}
            initialPosition={characterPanelInitialPos}
            pinned={characterPanelPinned}
            onPinnedChange={setCharacterPanelPinned}
            storageKey="outline-markdown-character-panel"
          >
            <div className="outline-character-panel-body">
              <CharacterTab bookId={bookId} hideHeader onActionActiveChange={handleCharacterActionActiveChange} />
            </div>
          </FloatingPanel>
        )}
        <OutlineHistoryDrawer
          outlineId={outlineId}
          open={historyOpen}
          onClose={() => setHistoryOpen(false)}
          onRestored={() => {
            // 回退会让正在编辑的草稿与最新内容失去对齐，统一退出编辑模式刷新
            setEditing(false)
            onSaved()
          }}
        />
      </div>
    )
  }
)

export default OutlineMarkdownPane
