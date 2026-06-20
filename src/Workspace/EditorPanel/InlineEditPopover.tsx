import React from 'react'
import { Button, Tooltip, Select, Switch, Divider } from 'antd'
import { CloseOutlined, CheckOutlined, RedoOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import StopCircleIcon from '../../icons/StopCircleIcon'
import type { AiModelConfig, EntityId, Outline } from '../../types'
import AiContextBar from '../AiPanel/components/AiContextBar'
import type { PromptTemplateContext } from '../AiPanel/promptTemplates'
import { buildInjectedContext } from './inlineEditContext'

export interface InlineCapture {
  text: string
  restore: () => void
  replace: (newText: string) => void
}

interface InlineEditPopoverProps {
  capture: InlineCapture
  initialPrompt: string
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  onSelectedModelChange: (id: string) => void
  thinkingEnabled: boolean
  onThinkingChange: (v: boolean) => void
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  bookTitle?: string
  onClose: () => void
  // 上下文：关联章节/大纲
  associatedChapterIds: EntityId[]
  setAssociatedChapterIds: (ids: EntityId[]) => void
  associatedOutlineIds: EntityId[]
  setAssociatedOutlineIds: (ids: EntityId[]) => void
  availableOutlines: Outline[]
  chapterSelectOptions: { value: EntityId; label: string }[]
  outlineSelectOptions: { value: EntityId; label: string }[]
  handleQuickAssociateChapter: () => void
  handleQuickAssociateOutline: () => void
  // 上下文：记忆/伏笔
  selectedMemoryIds: (number | string)[]
  selectedForeshadowingIds: (number | string)[]
  onOpenMemoryModal: () => void
}

export default function InlineEditPopover({
  capture,
  initialPrompt,
  modelConfigs,
  selectedModelId,
  onSelectedModelChange,
  thinkingEnabled,
  onThinkingChange,
  bookId,
  chapterId,
  chapterTitle,
  bookTitle,
  onClose,
  associatedChapterIds,
  setAssociatedChapterIds,
  associatedOutlineIds,
  setAssociatedOutlineIds,
  availableOutlines,
  chapterSelectOptions,
  outlineSelectOptions,
  handleQuickAssociateChapter,
  handleQuickAssociateOutline,
  selectedMemoryIds,
  selectedForeshadowingIds,
  onOpenMemoryModal,
}: InlineEditPopoverProps) {
  const [instruction, setInstruction] = React.useState(initialPrompt)
  const [result, setResult] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [contextPopoverOpen, setContextPopoverOpen] = React.useState(false)
  const chunkUnsubRef = React.useRef<(() => void) | null>(null)
  const popoverRef = React.useRef<HTMLDivElement>(null)
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)

  const model = React.useMemo(
    () => modelConfigs.find((m) => m.id === selectedModelId) ?? modelConfigs[0],
    [modelConfigs, selectedModelId]
  )

  const modelOptions = React.useMemo(
    () =>
      modelConfigs.map((c) => ({
        label: (c.nickname?.trim() || c.name) || '未命名',
        value: c.id,
      })),
    [modelConfigs]
  )

  const thinkingOnly = !!model?.thinkingOnly

  // 提示词模板上下文（含「选中」变量，使模板可引用当前选区文本）
  const promptTemplateContext = React.useMemo<PromptTemplateContext>(() => {
    const chapterTitles = associatedChapterIds
      .map(
        (id) =>
          chapterSelectOptions.find((o) => String(o.value) === String(id))
            ?.label ?? '',
      )
      .filter(Boolean)
    const outlineTitles = associatedOutlineIds
      .map(
        (id) =>
          outlineSelectOptions.find((o) => String(o.value) === String(id))
            ?.label ?? '',
      )
      .filter(Boolean)
    return {
      bookTitle,
      currentChapterTitle: chapterTitle,
      associatedChapterTitles: chapterTitles,
      associatedOutlineTitles: outlineTitles,
      selection: capture.text,
    }
  }, [
    associatedChapterIds,
    associatedOutlineIds,
    bookTitle,
    capture.text,
    chapterSelectOptions,
    chapterTitle,
    outlineSelectOptions,
  ])

  // 屏幕中心定位（保持简单、稳定；选区所在位置可能很下方，居中更保险）
  const style: React.CSSProperties = {
    position: 'fixed',
    top: '20vh',
    left: '50%',
    transform: 'translateX(-50%)',
  }

  const cleanupStream = React.useCallback(() => {
    chunkUnsubRef.current?.()
    chunkUnsubRef.current = null
  }, [])

  React.useEffect(() => {
    textareaRef.current?.focus()
    return () => {
      const hadActiveStream = chunkUnsubRef.current != null
      cleanupStream()
      if (hadActiveStream) {
        window.electronAPI.abortAiStream?.()
      }
    }
  }, [cleanupStream])

  const submit = React.useCallback(
    async (rawInstruction: string) => {
      const instr = rawInstruction.trim()
      if (!instr) return
      if (!model) {
        setError('请先在设置中添加模型')
        return
      }
      if (!model.apiKey?.trim()) {
        setError('请先在设置中添加模型并填写 API Key')
        return
      }
      setLoading(true)
      setResult('')
      setError(null)
      cleanupStream()

      const unsubscribe = window.electronAPI.onAiChunk((chunk) => {
        if (chunk.error) {
          setError('请求失败：' + chunk.error)
          setLoading(false)
          cleanupStream()
          return
        }
        if (chunk.delta) setResult((prev) => prev + chunk.delta)
        if (chunk.done) {
          setLoading(false)
          cleanupStream()
        }
      })
      chunkUnsubRef.current = unsubscribe

      const useConfiguredTemperature =
        model.customizeTemperature === undefined || model.customizeTemperature === true

      const useThinking = thinkingEnabled || thinkingOnly

      const systemPrompt = [
        '你是一位专业中文写作助手，负责对用户选中的文段进行改写。',
        bookTitle ? `当前作品：《${bookTitle}》` : '',
        chapterTitle ? `当前章节：${chapterTitle}` : '',
        '请严格按用户指令改写，**只输出改写后的纯文本**，不要加任何解释、标题、引号、Markdown 格式或前后缀。',
        '保持与原文相近的段落数量，除非用户明确要求调整。',
      ]
        .filter(Boolean)
        .join('\n')

      // 关联章节/大纲仍在客户端拼装；记忆/伏笔改走后端统一长期记忆编排。
      const injectedContext = await buildInjectedContext({
        bookId,
        userPrompt: instr,
        associatedChapterIds,
        associatedOutlineIds,
        availableOutlines,
        chapterSelectOptions,
        selectedMemoryIds,
        selectedForeshadowingIds,
      })

      const userPromptParts: string[] = []
      if (injectedContext) {
        userPromptParts.push('【参考资料（请仅作背景参考，不要原文复述）】')
        userPromptParts.push(injectedContext)
        userPromptParts.push('')
      }
      userPromptParts.push(`【改写指令】${instr}`)
      userPromptParts.push('')
      userPromptParts.push('【原文】')
      userPromptParts.push(capture.text)
      userPromptParts.push('')
      userPromptParts.push('请直接输出改写后的文本：')
      const userPrompt = userPromptParts.join('\n')

      window.electronAPI.aiChatStream({
        apiKey: model.apiKey,
        baseURL: model.baseUrl || undefined,
        apiProvider: model.apiProvider === 'anthropic' ? 'anthropic' : 'openai',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        options: {
          model: model.name,
          ...(useConfiguredTemperature
            ? {
                temperature: useThinking
                  ? model.temperatureThinking ?? 0.6
                  : model.temperatureNonThinking ?? 0.6,
              }
            : {}),
          thinking: { type: useThinking ? 'enabled' : 'disabled' },
          max_tokens: 4096,
        },
        tools: [],
        enableAgentTools: false,
        bookId: bookId ?? undefined,
        chapterId: chapterId ?? undefined,
        currentChapterTitle: chapterTitle || undefined,
        writingChapters: [],
        availableOutlines: [],
        chatAgentMode: 'ask',
      })
    },
    [
      associatedChapterIds,
      associatedOutlineIds,
      availableOutlines,
      bookId,
      bookTitle,
      capture.text,
      chapterId,
      chapterTitle,
      chapterSelectOptions,
      cleanupStream,
      model,
      selectedForeshadowingIds,
      selectedMemoryIds,
      thinkingEnabled,
      thinkingOnly,
    ]
  )

  const handleAbort = () => {
    cleanupStream()
    window.electronAPI.abortAiStream?.()
    setLoading(false)
  }

  const handleAccept = () => {
    const clean = result.trim()
    if (!clean) return
    window.dispatchEvent(new CustomEvent('inline-edit-accepted', {
      detail: { chapterId, source: 'inline_edit' },
    }))
    capture.replace(clean)
    onClose()
  }

  const handleRegenerate = () => {
    submit(instruction)
  }

  // 键盘：Esc 关闭；Ctrl/Cmd+Enter 提交；Ctrl/Cmd+Shift+Enter 接受
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault()
      if (e.shiftKey && result && !loading) {
        handleAccept()
      } else if (!loading) {
        submit(instruction)
      }
    }
  }

  return (
    <div
      ref={popoverRef}
      className="inline-edit-popover"
      style={style}
      onKeyDown={handleKeyDown}
    >
      <div className="inline-edit-popover-header">
        <span>Inline 改写</span>
        <Button
          type="text"
          size="small"
          icon={<CloseOutlined style={{ fontSize: 14 }} />}
          onClick={onClose}
        />
      </div>

      <div className="inline-edit-popover-body">
        <div className="inline-edit-popover-origin" title="原文">
          {capture.text}
        </div>

        <div className="inline-edit-popover-context-bar">
          <AiContextBar
            bookId={bookId}
            chapterId={chapterId}
            associatedChapterIds={associatedChapterIds}
            setAssociatedChapterIds={setAssociatedChapterIds}
            associatedOutlineIds={associatedOutlineIds}
            setAssociatedOutlineIds={setAssociatedOutlineIds}
            chapterSelectOptions={chapterSelectOptions}
            outlineSelectOptions={outlineSelectOptions}
            onQuickAssociateChapter={handleQuickAssociateChapter}
            onQuickAssociateOutline={handleQuickAssociateOutline}
            selectedMemoryIds={selectedMemoryIds}
            selectedForeshadowingIds={selectedForeshadowingIds}
            onOpenMemoryModal={onOpenMemoryModal}
            contextPopoverOpen={contextPopoverOpen}
            onContextPopoverOpenChange={setContextPopoverOpen}
            currentPrompt={instruction}
            onInsertPrompt={(text) => setInstruction(text)}
            promptTemplateContext={promptTemplateContext}
            promptTemplateDisabled={loading}
          />
        </div>

        <div className="inline-edit-popover-input-wrap">
          <textarea
            ref={textareaRef}
            className="inline-edit-popover-textarea"
            placeholder="告诉 AI 如何改写这段话... (Ctrl+Enter 生成)"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            rows={3}
            disabled={loading}
          />
        </div>

        {(loading || result || error) && (
          <div className="inline-edit-popover-result">
            {error && <div className="inline-edit-popover-error">{error}</div>}
            {loading && !result && !error && (
              <span className="inline-edit-popover-loading">思考中...</span>
            )}
            {result && (
              <div className="inline-edit-popover-result-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="inline-edit-popover-footer">
        <div className="inline-edit-popover-footer-left">
          <Select
            className="inline-edit-popover-model-select"
            size="small"
            value={modelOptions.length ? selectedModelId : undefined}
            onChange={onSelectedModelChange}
            options={modelOptions}
            placeholder={modelOptions.length ? undefined : '无模型配置'}
            variant="borderless"
            popupMatchSelectWidth={false}
            disabled={loading}
            popupRender={(menu) => (
              <>
                {menu}
                <Divider style={{ margin: '4px 0' }} />
                <div
                  className="inline-edit-popover-thinking-row"
                  onMouseDown={(e) => e.preventDefault()}
                >
                  <span>思考模式</span>
                  <Tooltip title={thinkingOnly ? '该模型不可关闭思考模式' : ''}>
                    <Switch
                      size="small"
                      checked={thinkingEnabled || thinkingOnly}
                      disabled={thinkingOnly || loading}
                      onChange={onThinkingChange}
                    />
                  </Tooltip>
                </div>
              </>
            )}
          />
        </div>
        <div className="inline-edit-popover-footer-right">
          {loading ? (
            <Button
              type="text"
              icon={<StopCircleIcon size={16} />}
              onClick={handleAbort}
              className="inline-edit-popover-stop"
            >
              停止
            </Button>
          ) : (
            <>
              {result && !error && (
                <>
                  <Button
                    type="text"
                    size="small"
                    icon={<RedoOutlined />}
                    onClick={handleRegenerate}
                  >
                    重新生成
                  </Button>
                  <Button
                    type="primary"
                    size="small"
                    icon={<CheckOutlined />}
                    onClick={handleAccept}
                  >
                    替换选中
                  </Button>
                </>
              )}
              {!result && (
                <Button
                  type="primary"
                  size="small"
                  onClick={() => submit(instruction)}
                  disabled={!instruction.trim() || !model}
                >
                  生成
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
