import React, { forwardRef, useImperativeHandle } from 'react'
import { EditOutlined, ImportOutlined } from '@ant-design/icons'
import { App as AntdApp, Button, Empty, Tooltip } from 'antd'
import MarkdownWithSearch from '../search/MarkdownWithSearch'
import { useWorkspace } from '../WorkspaceContext'
import type { Editor } from '@tiptap/core'
import { Extension } from '@tiptap/core'
import { useEditor, EditorContent } from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import { TableKit } from '@tiptap/extension-table'
import { markdownToHtml, htmlToMarkdown } from '../../utils/markdown'
import type { EntityId } from '../../types'
import './StoryBackgroundTab.scss'
import './OutlineMarkdownPane.scss'

/** Tab 键：列表内缩进，非列表插入制表符（与小说背景一致） */
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
    const { message: appMessage } = AntdApp.useApp()
    const { workspaceSearchQuery, notifyWorkspaceSearchContentChanged } = useWorkspace()

    React.useEffect(() => {
      notifyWorkspaceSearchContentChanged()
    }, [markdownContent, notifyWorkspaceSearchContentChanged])
    const [editing, setEditing] = React.useState(false)
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
        handlePaste: (_view, event) => {
          const text = event.clipboardData?.getData('text/plain') ?? ''
          if (!text.trim()) return false
          const looksLikeMarkdown =
            /^#+\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+|\n\s*[-*+]\s|\n#+\s|^>\s|^\s*\|.+\|/m.test(text)
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

    const [editorIsEmpty, setEditorIsEmpty] = React.useState(true)

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
          const res = await window.electronAPI.updateOutline({
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
      const res = await window.electronAPI.updateOutline({
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
      const res = await window.electronAPI.openAndReadTextFile()
      if (res.success && res.data != null) {
        const ed = editorRef.current
        if (ed) {
          const currentMd = htmlToMarkdown(ed.getHTML())
          const appended = currentMd.trim() ? `${currentMd}\n\n${res.data}` : res.data
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
              <Tooltip title="编辑">
                <Button type="text" size="small" icon={<EditOutlined />} onClick={handleEdit} />
              </Tooltip>
            </div>
          </div>
          <div className="story-background-content story-background-markdown">
            <MarkdownWithSearch content={content} searchQuery={workspaceSearchQuery} />
          </div>
        </div>
      )
    }

    return (
      <div className="outline-markdown-pane outline-markdown-pane--editing story-background-editing">
        <div className="story-background-editing-header">
          <div className="story-background-toolbar story-background-toolbar-top">
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
      </div>
    )
  }
)

export default OutlineMarkdownPane
