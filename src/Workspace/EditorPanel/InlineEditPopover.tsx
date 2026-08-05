import { services } from '@/services'
import React from 'react'
import { PurrButton } from '@/purr-components'
import { CloseIcon, CheckIcon, RedoIcon } from '@/purr-components'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { StopCircleIcon } from '@/purr-components'
import type { AiModelConfig, EntityId, Outline } from '../../types'
import AiContextBar from '../AiPanel/components/AiContextBar'
import type { PromptTemplateContext } from '../AiPanel/promptTemplates'
import ModelPicker from '../AiPanel/components/ModelPicker'
import { buildInjectedContext } from './inlineEditContext'
import { normalizeApiProvider } from '../../modelCatalog'
import { createAiStreamId } from '../../utils/aiStream'
import { buildStreamOptions } from '../AiPanel/hooks/streamOptions'

export interface InlineCapture {
  text: string
  restore: () => void
  replace: (newText: string) => void
}

interface InlineEditPopoverProps {
  capture: InlineCapture
  initialPrompt: string
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled'>>) => void
  selectedModelId: string
  onSelectedModelChange: (id: string) => void
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
  onUpdateModelConfig,
  selectedModelId,
  onSelectedModelChange,
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
  const streamIdRef = React.useRef<string | null>(null)
  const popoverRef = React.useRef<HTMLDivElement>(null)
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)

  const model = React.useMemo(
    () => modelConfigs.find((m) => m.id === selectedModelId) ?? modelConfigs[0],
    [modelConfigs, selectedModelId]
  )

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
        services.ai.abortAiStream?.(streamIdRef.current ?? undefined)
      }
      streamIdRef.current = null
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
      if (streamIdRef.current) {
        services.ai.abortAiStream?.(streamIdRef.current)
      }
      cleanupStream()

      const streamId = createAiStreamId('inline-edit')
      streamIdRef.current = streamId
      const unsubscribe = services.ai.onAiChunk((chunk) => {
        if (chunk.error) {
          setError('请求失败：' + chunk.error)
          setLoading(false)
          cleanupStream()
          streamIdRef.current = null
          return
        }
        if (chunk.delta) setResult((prev) => prev + chunk.delta)
        if (chunk.done) {
          setLoading(false)
          cleanupStream()
          streamIdRef.current = null
        }
      }, streamId)
      chunkUnsubRef.current = unsubscribe

      const { options: streamOptions } = buildStreamOptions({
        cfg: model,
        selectedModel: model.id,
      })
      const contextWindow = streamOptions.context_window

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
        contextWindow,
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

      services.ai.aiChatStream({
        streamId,
        apiKey: model.apiKey,
        baseURL: model.baseUrl || undefined,
        apiProvider: normalizeApiProvider(model.apiProvider),
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userPrompt },
        ],
        options: streamOptions,
        enableAgentTools: false,
        bookId: bookId ?? undefined,
        chapterId: chapterId ?? undefined,
        currentChapterTitle: chapterTitle || undefined,
        chatAgentMode: 'ask',
        contextWindow,
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
    ]
  )

  const handleAbort = () => {
    cleanupStream()
    services.ai.abortAiStream?.(streamIdRef.current ?? undefined)
    streamIdRef.current = null
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
        <PurrButton
          type="text"
          size="small"
          icon={<CloseIcon style={{ fontSize: 14 }} />}
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
          <ModelPicker
            modelConfigs={modelConfigs}
            selectedModelId={selectedModelId}
            onModelChange={onSelectedModelChange}
            onUpdateModelConfig={onUpdateModelConfig}
            disabled={loading}
            className="inline-edit-popover-model-select"
          />
        </div>
        <div className="inline-edit-popover-footer-right">
          {loading ? (
            <PurrButton
              type="text"
              icon={<StopCircleIcon size={16} />}
              onClick={handleAbort}
              className="inline-edit-popover-stop"
            >
              停止
            </PurrButton>
          ) : (
            <>
              {result && !error && (
                <>
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<RedoIcon />}
                    onClick={handleRegenerate}
                  >
                    重新生成
                  </PurrButton>
                  <PurrButton
                    type="primary"
                    size="small"
                    icon={<CheckIcon />}
                    onClick={handleAccept}
                  >
                    替换选中
                  </PurrButton>
                </>
              )}
              {!result && (
                <PurrButton
                  type="primary"
                  size="small"
                  onClick={() => submit(instruction)}
                  disabled={!instruction.trim() || !model}
                >
                  生成
                </PurrButton>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
