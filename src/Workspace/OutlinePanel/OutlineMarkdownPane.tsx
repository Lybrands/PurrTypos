import { services } from '@/services'
import React, { forwardRef, useImperativeHandle } from 'react'
import { EditIcon, HistoryIcon, ImportIcon, UserIcon } from '@/purr-components'
import { PurrButton, PurrEmpty, PurrTooltip, usePurrToast } from '@/purr-components'
import { PurrFloatingPanel } from '@/purr-components'
import KnowledgeMarkdownEditor, {
  appendImportedMarkdown,
  type KnowledgeMarkdownEditorHandle,
} from '@/components/KnowledgeMarkdownEditor'
import MarkdownWithSearch from '../search/MarkdownWithSearch'
import OutlineHistoryDrawer from './OutlineHistoryDrawer'
import {
    notifyWorkspaceSearchContentChanged,
    useBookId,
    useSearchQuery,
  } from '../../stores/workspaceStore'
import type { EntityId } from '../../types'
import CharacterTab from './CharacterTab'
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
    const appMessage = usePurrToast()
    const bookId = useBookId()
    const workspaceSearchQuery = useSearchQuery()

    React.useEffect(() => {
      notifyWorkspaceSearchContentChanged()
    }, [markdownContent, notifyWorkspaceSearchContentChanged])
    const [editing, setEditing] = React.useState(false)
    const [historyOpen, setHistoryOpen] = React.useState(false)
    const editingRef = React.useRef(false)
    React.useEffect(() => {
      editingRef.current = editing
    }, [editing])

    const [draftMarkdown, setDraftMarkdown] = React.useState('')
    const baselineSavedRef = React.useRef(markdownContent ?? '')
    React.useEffect(() => {
      baselineSavedRef.current = markdownContent ?? ''
    }, [markdownContent])

    const editorRef = React.useRef<KnowledgeMarkdownEditorHandle | null>(null)
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

    useImperativeHandle(
      ref,
      () => ({
        async flushSave() {
          if (!editingRef.current || !editorRef.current) return
          const md = editorRef.current.getMarkdown()
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
      setDraftMarkdown(markdownContent ?? '')
      setEditing(true)
    }

    const handleSave = React.useCallback(async () => {
      const md = editorRef.current?.getMarkdown() ?? draftMarkdown
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
    }, [outlineId, onSaved, appMessage, draftMarkdown])

    const handleCancel = () => {
      setEditing(false)
    }

    const handleImportFile = React.useCallback(async () => {
      const res = await services.files.openAndReadTextFile()
      if (res.success && res.data != null) {
        setDraftMarkdown((current) => appendImportedMarkdown(current, res.data ?? ''))
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
            <PurrEmpty
              image={PurrEmpty.PRESENTED_IMAGE_SIMPLE}
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
              <PurrButton type="primary" size="small" onClick={handleEdit}>
                开始编写
              </PurrButton>
            </PurrEmpty>
          </div>
        )
      }
      return (
        <div className="outline-markdown-pane outline-markdown-pane--view story-background-view">
          <div className="story-background-view-header">
            <div className="story-background-toolbar story-background-toolbar-top">
              <PurrTooltip title="人物">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<UserIcon />}
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
              </PurrTooltip>
              <PurrTooltip title="历史">
                <PurrButton
                  type="text"
                  size="small"
                  icon={<HistoryIcon />}
                  onClick={() => setHistoryOpen(true)}
                />
              </PurrTooltip>
              <PurrTooltip title="编辑">
                <PurrButton type="text" size="small" icon={<EditIcon />} onClick={handleEdit} />
              </PurrTooltip>
            </div>
          </div>
          <div className="story-background-content story-background-markdown">
            <MarkdownWithSearch content={content} searchQuery={workspaceSearchQuery} />
          </div>
          {characterPanelOpen && (
            <PurrFloatingPanel
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
            </PurrFloatingPanel>
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
            <PurrTooltip title="人物">
              <PurrButton
                type="text"
                size="small"
                icon={<UserIcon />}
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
            </PurrTooltip>
            <PurrTooltip title="历史">
              <PurrButton
                type="text"
                size="small"
                icon={<HistoryIcon />}
                onClick={() => setHistoryOpen(true)}
              />
            </PurrTooltip>
            <PurrTooltip title="导入文件">
              <PurrButton type="text" size="small" icon={<ImportIcon />} onClick={handleImportFile} />
            </PurrTooltip>
          </div>
        </div>
        <KnowledgeMarkdownEditor
          ref={editorRef}
          documentKey={`outline:${outlineId}`}
          value={draftMarkdown}
          onChange={setDraftMarkdown}
          ariaLabel="章节大纲"
          className="story-background-editor-wrap"
        />
        <div className="story-background-toolbar story-background-toolbar-bottom">
          <PurrButton type="primary" size="small" onClick={handleSave}>
            保存
          </PurrButton>
          <PurrButton size="small" onClick={handleCancel}>
            取消
          </PurrButton>
        </div>
        {characterPanelOpen && (
          <PurrFloatingPanel
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
          </PurrFloatingPanel>
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
