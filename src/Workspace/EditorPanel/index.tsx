/// <reference path="../../vite-env.d.ts" />
import React from 'react'
import {
  ExpandOutlined, CompressOutlined, CloseOutlined,
  UndoOutlined, RedoOutlined, AlignLeftOutlined,
  CopyOutlined,
  BorderlessTableOutlined,
  HistoryOutlined,
} from '@ant-design/icons'
import { App as AntdApp, Button, Input, Empty, Tooltip, Select, Switch } from 'antd'
import type { TextAreaRef } from 'antd/es/input/TextArea'
import type { AiModelConfig, EntityId } from '../../types'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useWorkspace } from '../WorkspaceContext'
import LexicalEditorComponent, { type LexicalEditorHandle } from './LexicalEditor'
import StopCircleIcon from '../../icons/StopCircleIcon'
import { useDiff } from '../diff/DiffContext'
import DiffOverlay from '../diff/DiffOverlay'
import DiffHistoryDrawer from '../diff/DiffHistoryDrawer'
import InlineEditLayer from './InlineEditLayer'
import GhostCompletion, { type GhostTrigger } from './GhostCompletion'
import './index.scss'

const AUTOSAVE_DELAY = 800

/**
 * 剔除章节标题里开头的「第 xx 章」前缀，便于复制时只保留纯标题正文。
 * 支持：
 * - 阿拉伯数字 / 全角数字（第 12 章、第１２章）
 * - 中文数字（第一章 / 第一百零八章 / 第两章 / 第〇章）
 * - 章号与章字、标题之间的分隔符：空格、冒号、句点、破折号、中点、下划线等
 * 若剥离后为空（整标题本身就是「第 X 章」），则回退为原始标题，避免复制出空串。
 */
function stripChapterPrefix(title: string): string {
  if (!title) return title
  const stripped = title.replace(
    /^\s*第\s*[0-9０-９一二三四五六七八九十百千万亿零〇两]+\s*章[\s:：.。、．\-—–·_　]*/,
    '',
  )
  const trimmed = stripped.trim()
  return trimmed || title.trim()
}

interface AiFloatState {
  visible: boolean; x: number; y: number
  prompt: string; loading: boolean; result: string
  selectedModelId: string
  thinkingEnabled: boolean
}

interface EditorPanelProps {
  bookTitle: string
  modelConfigs?: AiModelConfig[]
  /** 是否为当前主区域（占 56%）。 */
  isMain: boolean
  /** 点击 ⤢ 时回调：非主时切换为主，主时回到默认。 */
  onSetMain: () => void
  /** 工作台搜索：注册 Lexical 实例 */
  onLexicalEditor?: (editor: import('lexical').LexicalEditor | null) => void
}

export default function EditorPanel({
  bookTitle: _bookTitle,
  modelConfigs = [],
  isMain,
  onSetMain,
  onLexicalEditor,
}: EditorPanelProps) {
  const { message: appMessage } = AntdApp.useApp()
  const {
    writingChapters: chapters,
    activeChapterId: chapterId,
    activeChapterTitle: chapterTitle,
    bookId,
    notifyWorkspaceSearchContentChanged,
  } = useWorkspace()
  const diff = useDiff()
  const diffActive = chapterId != null && diff.hasSession(chapterId)
  const [diffHistoryOpen, setDiffHistoryOpen] = React.useState(false)

  /** Inline Edit：选区状态 */
  const [inlineSelection, setInlineSelection] = React.useState<
    { text: string; rect: DOMRect } | null
  >(null)
  const handleSelectionChange = React.useCallback(
    (payload: { text: string; rect: DOMRect } | null) => {
      setInlineSelection(payload)
    },
    []
  )
  const clearInlineSelection = React.useCallback(() => {
    setInlineSelection(null)
  }, [])
  /** diff 时禁用 Inline Edit */
  const inlineEditEnabled = !diffActive

  /** Ghost text 续写：与 Inline Edit 同生命周期条件 */
  const [ghostTrigger, setGhostTrigger] = React.useState<GhostTrigger | null>(null)
  const ghostTokenRef = React.useRef(0)
  const handleGhostIdle = React.useCallback(
    (payload: { prefix: string; cursorRect: DOMRect }) => {
      ghostTokenRef.current += 1
      setGhostTrigger({ token: ghostTokenRef.current, ...payload })
    },
    []
  )
  const handleGhostReset = React.useCallback(() => {
    setGhostTrigger(null)
  }, [])
  const handleGhostAccept = React.useCallback((text: string) => {
    if (text) lexicalEditorRef.current?.insertAtCursor(text)
    setGhostTrigger(null)
  }, [])
  const searchNotifyTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const [content, setContent] = React.useState('')
  const [saveStatus, setSaveStatus] = React.useState('已保存')
  const [wordCount, setWordCount] = React.useState(0)

  const initialModelId = modelConfigs[0]?.id ?? ''
  const [aiFloat, setAiFloat] = React.useState<AiFloatState>(
    {
      visible: false,
      x: 0,
      y: 0,
      prompt: '',
      loading: false,
      result: '',
      selectedModelId: initialModelId,
      thinkingEnabled: false,
    }
  )
  const aiChunkUnsubRef = React.useRef<(() => void) | null>(null)

  const saveTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastChapterIdRef = React.useRef<EntityId | null>(null)
  const lexicalEditorRef = React.useRef<LexicalEditorHandle>(null)

  const refreshArticle = React.useCallback((cid: EntityId) => {
    window.electronAPI.getArticle({ chapterId: cid }).then((res) => {
      const text = res.success && res.data ? res.data.content : ''
      setContent(text)
      setWordCount(text.replace(/\s/g, '').length)
    })
  }, [])

  React.useEffect(() => {
    if (chapterId === lastChapterIdRef.current) return
    lastChapterIdRef.current = chapterId
    if (!chapterId) { setContent(''); setWordCount(0); return }
    refreshArticle(chapterId)
  }, [chapterId, refreshArticle])

  React.useEffect(() => {
    const handler = (e: Event) => {
      const { chapterId: updatedId } = (e as CustomEvent<{ chapterId: EntityId }>).detail ?? {}
      if (updatedId != null && updatedId === chapterId) refreshArticle(updatedId)
    }
    window.addEventListener('chapter-content-updated', handler)
    return () => window.removeEventListener('chapter-content-updated', handler)
  }, [chapterId, refreshArticle])

  const scheduleAutoSave = React.useCallback((text: string) => {
    if (!chapterId) return
    setSaveStatus('保存中...')
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      const res = await window.electronAPI.saveArticle({ chapterId, content: text })
      setSaveStatus(res.success ? '已保存' : '保存失败')
    }, AUTOSAVE_DELAY)
  }, [chapterId])

  const handleContentChange = React.useCallback((text: string) => {
    setContent(text)
    setWordCount(text.replace(/\s/g, '').length)
    scheduleAutoSave(text)
    if (searchNotifyTimerRef.current) clearTimeout(searchNotifyTimerRef.current)
    searchNotifyTimerRef.current = setTimeout(() => {
      notifyWorkspaceSearchContentChanged()
    }, 200)
  }, [scheduleAutoSave, notifyWorkspaceSearchContentChanged])

  const handleCopyToClipboard = React.useCallback(async (text: string, label: string) => {
    if (!text.trim()) {
      appMessage.info(`${label}为空，没有可复制的内容`)
      return
    }
    try {
      await navigator.clipboard.writeText(text)
      appMessage.success(`已复制${label}`)
    } catch (err) {
      console.error('[EditorPanel] clipboard write failed:', err)
      appMessage.error(`复制${label}失败，请手动复制`)
    }
  }, [appMessage])

  const handleReformat = React.useCallback(() => {
    const result = lexicalEditorRef.current?.reformat()
    if (!result) return
    if (!result.changed) {
      appMessage.info('已是规范排版，无需调整')
      return
    }
    const parts: string[] = []
    if (result.strippedIndents > 0) parts.push(`去除 ${result.strippedIndents} 处首行空白`)
    if (result.removedEmptyLines > 0) parts.push(`删除 ${result.removedEmptyLines} 行空行`)
    appMessage.success(
      parts.length ? `已排版：${parts.join('、')}（Ctrl+Z 可撤销）` : '已排版（Ctrl+Z 可撤销）',
    )
  }, [appMessage])

  /** 命令面板派发的事件监听 */
  React.useEffect(() => {
    const onOpenHistory = () => {
      if (chapterId == null) {
        appMessage.info('请先选择章节')
        return
      }
      setDiffHistoryOpen(true)
    }
    const onReformat = () => {
      if (chapterId == null) {
        appMessage.info('请先选择章节')
        return
      }
      handleReformat()
    }
    const onCopyTitle = () => handleCopyToClipboard(stripChapterPrefix(chapterTitle ?? ''), '标题')
    const onCopyContent = () => handleCopyToClipboard(content ?? '', '正文')

    window.addEventListener('editor-open-diff-history', onOpenHistory)
    window.addEventListener('editor-reformat', onReformat)
    window.addEventListener('editor-copy-title', onCopyTitle)
    window.addEventListener('editor-copy-content', onCopyContent)
    return () => {
      window.removeEventListener('editor-open-diff-history', onOpenHistory)
      window.removeEventListener('editor-reformat', onReformat)
      window.removeEventListener('editor-copy-title', onCopyTitle)
      window.removeEventListener('editor-copy-content', onCopyContent)
    }
  }, [chapterId, chapterTitle, content, appMessage, handleCopyToClipboard, handleReformat])

  const handleKeyTrigger = React.useCallback((key: string, rect: DOMRect) => {
    if (key === 'backslash') {
      setAiFloat((prev) => ({
        ...prev,
        visible: true,
        x: rect.left + 40,
        y: rect.top + 60,
        prompt: '',
        loading: false,
        result: '',
      }))
    }
    if (key === 'escape') {
      setAiFloat((prev) => ({ ...prev, visible: false, x: 0, y: 0, prompt: '', loading: false }))
    }
  }, [])

  React.useEffect(() => {
    if (!modelConfigs.length) return
    setAiFloat((prev) => {
      if (modelConfigs.some((m) => m.id === prev.selectedModelId)) return prev
      return { ...prev, selectedModelId: modelConfigs[0].id }
    })
  }, [modelConfigs])

  React.useEffect(() => {
    return () => {
      aiChunkUnsubRef.current?.()
      aiChunkUnsubRef.current = null
      window.electronAPI.abortAiStream()
    }
  }, [])

  const closeAiFloat = () => {
    aiChunkUnsubRef.current?.()
    aiChunkUnsubRef.current = null
    if (aiFloat.loading) window.electronAPI.abortAiStream()
    setAiFloat((prev) => ({ ...prev, visible: false, x: 0, y: 0, loading: false }))
  }

  const selectedModelConfig = React.useMemo(
    () => modelConfigs.find((m) => m.id === aiFloat.selectedModelId) ?? modelConfigs[0],
    [modelConfigs, aiFloat.selectedModelId]
  )

  const handleAiFloatAbort = React.useCallback(() => {
    aiChunkUnsubRef.current?.()
    aiChunkUnsubRef.current = null
    window.electronAPI.abortAiStream()
    setAiFloat((prev) => ({ ...prev, loading: false }))
  }, [])

  const handleAiFloatSubmit = () => {
    if (!aiFloat.prompt.trim()) return
    if (!selectedModelConfig?.apiKey?.trim()) {
      setAiFloat((prev) => ({ ...prev, result: '请先在设置中添加模型并填写 API Key', loading: false }))
      return
    }
    setAiFloat((prev) => ({ ...prev, loading: true, result: '' }))

    aiChunkUnsubRef.current?.()
    const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
      if (chunk.toolRouterWarning) {
        appMessage.warning(chunk.toolRouterWarning)
      }
      if (chunk.error) {
        setAiFloat((prev) => ({ ...prev, loading: false, result: '请求失败：' + chunk.error }))
        unsubscribe()
        aiChunkUnsubRef.current = null
        return
      }
      if (chunk.delta) {
        setAiFloat((prev) => ({ ...prev, result: prev.result + chunk.delta }))
      }
      if (chunk.done) {
        setAiFloat((prev) => ({ ...prev, loading: false }))
        unsubscribe()
        aiChunkUnsubRef.current = null
      }
    })
    aiChunkUnsubRef.current = unsubscribe

    const useConfiguredTemperature =
      selectedModelConfig.customizeTemperature === undefined ||
      selectedModelConfig.customizeTemperature === true

    const streamOptions: {
      model: string
      temperature?: number
      thinking: { type: 'enabled' | 'disabled' }
      max_tokens: number
    } = {
      model: selectedModelConfig.name,
      ...(useConfiguredTemperature
        ? {
            temperature: aiFloat.thinkingEnabled
              ? (selectedModelConfig.temperatureThinking ?? 0.6)
              : (selectedModelConfig.temperatureNonThinking ?? 0.6),
          }
        : {}),
      thinking: {
        type: (aiFloat.thinkingEnabled ? 'enabled' : 'disabled') as 'enabled' | 'disabled',
      },
      max_tokens: 8192,
    }

    window.electronAPI.aiChatStream({
      apiKey: selectedModelConfig.apiKey,
      baseURL: selectedModelConfig.baseUrl || undefined,
      apiProvider:
        selectedModelConfig.apiProvider === 'anthropic' ? 'anthropic' : 'openai',
      messages: [
        { role: 'system', content: '你是一位专业写作助手，请根据用户需求提供写作建议或内容。' },
        { role: 'user', content: aiFloat.prompt },
      ],
      options: streamOptions,
      tools: [],
      useToolRouter: false,
      bookId: bookId ?? undefined,
      chapterId: chapterId ?? undefined,
      currentChapterTitle: chapterTitle || undefined,
      writingChapters: chapters.map((c) => ({ id: c.id, title: c.title })),
      availableOutlines: [],
      agentMode: 'legacy',
      chatAgentMode: 'ask',
    })
  }

  return (
    <div className={`editor-panel ${isMain ? 'panel-main' : ''}`}>
      <div className="panel-header">
        <span className="panel-title">{chapterTitle || '选择章节开始写作'}</span>
        <div className="panel-header-actions">
          {/*
           * diff 历史回滚按钮：始终可见且可点击，
           * 即使未选章节，也允许点开提示用户"请先选择章节"，
           * 这是 AI 误改正文后唯一的兜底入口，不能被任何状态遮蔽。
           */}
          <Tooltip title="查看本章 diff 历史 / 回滚">
            <Button
              type="text"
              size="small"
              icon={<HistoryOutlined style={{ fontSize: 14 }} />}
              onClick={() => {
                if (chapterId == null) {
                  appMessage.info('请先选择章节，再查看 diff 历史')
                  return
                }
                setDiffHistoryOpen(true)
              }}
            />
          </Tooltip>
          <Button type="text" size="small"
            icon={isMain ? <CompressOutlined style={{ fontSize: 16 }} /> : <ExpandOutlined style={{ fontSize: 16 }} />}
            title={isMain ? '已是主区域' : '扩大此区域为主'} onClick={onSetMain} />
        </div>
      </div>

      <DiffHistoryDrawer
        chapterId={chapterId ?? null}
        chapterTitle={chapterTitle ?? ''}
        open={diffHistoryOpen}
        onClose={() => setDiffHistoryOpen(false)}
      />

      <div className="editor-main">
        {diffActive && chapterId != null && (
          <DiffOverlay chapterId={chapterId} chapterTitle={chapterTitle ?? ''} />
        )}
        {!chapterId ? (
          <Empty image={false} description={
            <><p>从导演笔记本选择章节</p><small>点击左侧「章节」分组中的章节可切换，输入 <kbd>\</kbd> 可唤起 AI 助手</small></>
          } className="editor-empty" />
        ) : (
          <>
            <LexicalEditorComponent
              ref={lexicalEditorRef}
              key={chapterId}
              value={content}
              chapterId={chapterId}
              onChange={handleContentChange}
              onKeyTrigger={handleKeyTrigger}
              placeholder={`开始写作「${chapterTitle}」... 提示：输入 \\ 可唤起 AI 助手`}
              className="editor-lexical-wrap"
              onLexicalEditor={onLexicalEditor}
              onSelectionChange={inlineEditEnabled ? handleSelectionChange : undefined}
              ghostEnabled={inlineEditEnabled}
              onGhostIdle={inlineEditEnabled ? handleGhostIdle : undefined}
              onGhostReset={inlineEditEnabled ? handleGhostReset : undefined}
            />
            <div className="editor-footer">
              <div className="editor-footer-left">
                <Tooltip title="撤回 (Ctrl+Z)">
                  <Button type="text" size="small" icon={<UndoOutlined />} className="editor-toolbar-btn"
                    onClick={() => lexicalEditorRef.current?.undo()} />
                </Tooltip>
                <Tooltip title="前进 (Ctrl+Shift+Z)">
                  <Button type="text" size="small" icon={<RedoOutlined />} className="editor-toolbar-btn"
                    onClick={() => lexicalEditorRef.current?.redo()} />
                </Tooltip>
                <Tooltip title="一键排版">
                  <Button type="text" size="small" icon={<AlignLeftOutlined />} className="editor-toolbar-btn"
                    onClick={handleReformat} />
                </Tooltip>
                <Tooltip title="复制标题">
                  <Button type="text" size="small" icon={<BorderlessTableOutlined />} className="editor-toolbar-btn"
                    onClick={() => handleCopyToClipboard(stripChapterPrefix(chapterTitle ?? ''), '标题')} />
                </Tooltip>
                <Tooltip title="复制正文">
                  <Button type="text" size="small" icon={<CopyOutlined />} className="editor-toolbar-btn"
                    onClick={() => handleCopyToClipboard(content ?? '', '正文')} />
                </Tooltip>
              </div>
              <div className="editor-footer-right">
                <span className="word-count">{wordCount} 字</span>
                <span className={`save-status ${saveStatus === '保存失败' ? 'save-error' : ''}`}>{saveStatus}</span>
              </div>
            </div>
          </>
        )}
      </div>

      {inlineEditEnabled && (
        <InlineEditLayer
          lexicalRef={lexicalEditorRef}
          selection={inlineSelection}
          onClearSelection={clearInlineSelection}
          modelConfigs={modelConfigs}
          selectedModelId={aiFloat.selectedModelId}
          onSelectedModelChange={(id) =>
            setAiFloat((prev) => ({ ...prev, selectedModelId: id }))
          }
          thinkingEnabled={aiFloat.thinkingEnabled}
          onThinkingChange={(v) =>
            setAiFloat((prev) => ({ ...prev, thinkingEnabled: v }))
          }
          bookId={bookId}
          chapterId={chapterId}
          chapterTitle={chapterTitle ?? ''}
          bookTitle={_bookTitle}
          writingChapters={chapters.map((c) => ({ id: c.id, title: c.title }))}
        />
      )}

      {inlineEditEnabled && (
        <GhostCompletion
          trigger={ghostTrigger}
          model={modelConfigs.find((m) => m.id === aiFloat.selectedModelId) ?? modelConfigs[0]}
          bookId={bookId}
          chapterId={chapterId}
          chapterTitle={chapterTitle ?? ''}
          bookTitle={_bookTitle}
          onAccept={handleGhostAccept}
          onCancel={handleGhostReset}
        />
      )}

      {aiFloat.visible && (
        <AiFloatBox
          x={aiFloat.x} y={aiFloat.y}
          prompt={aiFloat.prompt}
          loading={aiFloat.loading}
          result={aiFloat.result}
          modelConfigs={modelConfigs}
          selectedModelId={aiFloat.selectedModelId}
          thinkingEnabled={aiFloat.thinkingEnabled}
          onPromptChange={(v) => setAiFloat((prev) => ({ ...prev, prompt: v }))}
          onModelChange={(v) => setAiFloat((prev) => ({ ...prev, selectedModelId: v }))}
          onThinkingChange={(v) => setAiFloat((prev) => ({ ...prev, thinkingEnabled: v }))}
          onSubmit={handleAiFloatSubmit}
          onAbort={handleAiFloatAbort}
          onClose={closeAiFloat}
        />
      )}
    </div>
  )
}

interface AiFloatBoxProps {
  x: number
  y: number
  prompt: string
  loading: boolean
  result: string
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  thinkingEnabled: boolean
  onPromptChange: (v: string) => void
  onModelChange: (v: string) => void
  onThinkingChange: (v: boolean) => void
  onSubmit: () => void
  onAbort: () => void
  onClose: () => void
}

function AiFloatBox({
  x,
  y,
  prompt,
  loading,
  result,
  modelConfigs,
  selectedModelId,
  thinkingEnabled,
  onPromptChange,
  onModelChange,
  onThinkingChange,
  onSubmit,
  onAbort,
  onClose,
}: AiFloatBoxProps) {
  const textareaRef = React.useRef<TextAreaRef>(null)
  React.useEffect(() => { textareaRef.current?.focus() }, [])
  const modelOptions = React.useMemo(
    () => modelConfigs.map((c) => ({ label: (c.nickname?.trim() || c.name) || '未命名', value: c.id })),
    [modelConfigs]
  )

  return (
    <div className="ai-float-box" style={{ left: x, top: y }}>
      <div className="ai-float-header">
        <span>AI 写作助手</span>
        <Button type="text" size="small" icon={<CloseOutlined style={{ fontSize: 16 }} />} onClick={onClose} className="btn-close" />
      </div>
      <div className="ai-float-input-row ai-float-input-row--textarea">
        <Input.TextArea
          ref={textareaRef}
          className="ai-float-textarea"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          placeholder="告诉 AI 你要润色、续写或改写的需求..."
          autoSize={{ minRows: 2, maxRows: 6 }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              onSubmit()
            }
            if (e.key === 'Escape') onClose()
          }}
          disabled={loading}
        />
      </div>
      <div className="ai-float-bottom">
        <div className="ai-float-bottom-left">
          <Select
            className="ai-float-model-select"
            size="small"
            value={modelOptions.length ? selectedModelId : undefined}
            onChange={onModelChange}
            options={modelOptions}
            placeholder={modelOptions.length ? undefined : '无模型配置'}
            variant="borderless"
            popupMatchSelectWidth={false}
          />
          <span className="ai-float-thinking-label">思考模式</span>
          <Switch size="small" checked={thinkingEnabled} onChange={onThinkingChange} />
        </div>
        <div className="ai-float-bottom-right">
          {loading ? (
            <Button
              className="btn-submit btn-stop"
              icon={<StopCircleIcon size={18} />}
              type="text"
              onClick={onAbort}
            />
          ) : (
            <Button type="primary" size="small" onClick={onSubmit} disabled={!prompt.trim()}>
              发送
            </Button>
          )}
        </div>
      </div>
      {(loading || result) && (
        <div className="ai-float-result">
          {loading && !result ? (
            <span className="a-loading-dots">思考中...</span>
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
          )}
        </div>
      )}
    </div>
  )
}
