import { services } from '@/services'
import React from 'react'
import { PurrButton, PurrTooltip } from '@/purr-components'
import {
  CheckIcon,
  CloseIcon,
  HighlightIcon,
  ImportIcon,
  RedoIcon,
} from '@/purr-components'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { StopCircleIcon } from '@/purr-components'
import type { AiModelConfig, EntityId, Outline } from '../../types'
import AiContextBar from '../AiPanel/components/AiContextBar'
import type { PromptTemplateContext } from '../AiPanel/promptTemplates'
import ModelPicker from '../../components/AgentConversation/Composer/ModelPicker'
import { buildInjectedContext } from './inlineEditContext'
import { normalizeGeneratedPlainText } from './generationText'
import { normalizeApiProvider } from '../../modelCatalog'
import { createAiStreamId } from '../../utils/aiStream'
import { buildStreamOptions } from '../../agent-runtime/streamOptions'
import type { InlineApplyResult } from './plugins/EditorHandlePlugin'

export interface InlineCapture {
  text: string
  flatStart: number
  flatEnd: number
  restore: () => void
  replace: (newText: string) => InlineApplyResult
  insertAfter: (newText: string) => InlineApplyResult
}

/** 改写模式内的快捷预设指令（原选区工具条入口收敛到弹层内） */
export const PRESETS: { id: string; label: string; prompt: string }[] = [
  {
    id: 'polish',
    label: '润色',
    prompt: '对下面这段话进行润色，让文字更流畅、生动，保持原意不变，不要拉长篇幅。',
  },
  {
    id: 'shorten',
    label: '精简',
    prompt: '把下面这段话改得更精简凝练，保留核心信息与语气，去除冗余与废话。',
  },
  {
    id: 'expand',
    label: '扩写',
    prompt: '对下面这段话进行合理扩写，增加画面感与细节描写，保持原风格与语气一致。',
  },
]

interface InlineEditPopoverProps {
  capture: InlineCapture
  initialPrompt: string
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort'>>) => void
  selectedModelId: string
  onSelectedModelChange: (id: string) => void
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  bookTitle?: string
  /** 读取编辑器当前章节全文（含未保存修改），用于注入【当前章节】上下文 */
  getCurrentChapterText: () => string
  onClose: () => void
  /** 「存为批注」：把回答预填进批注弹层（由外层接管锚点与保存） */
  onSaveAnnotation?: (answer: string) => void
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
  selectedLongTermMemoryIds: string[]
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
  getCurrentChapterText,
  onClose,
  onSaveAnnotation,
  associatedChapterIds,
  setAssociatedChapterIds,
  associatedOutlineIds,
  setAssociatedOutlineIds,
  availableOutlines,
  chapterSelectOptions,
  outlineSelectOptions,
  handleQuickAssociateChapter,
  handleQuickAssociateOutline,
  selectedLongTermMemoryIds,
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
      if (!contextWindow) throw new Error('请先配置模型上下文窗口')

      // 统一契约：指令可能是改写指令，也可能是提问——按指令意图自行决定输出形态；
      // 结果区同时提供 替换/插入/存为批注，怎么用由用户决定
      const finalSystem = [
        '你是一位专业中文写作助手，围绕用户选中的文段工作。',
        bookTitle ? `当前作品：《${bookTitle}》` : '',
        chapterTitle ? `当前章节：${chapterTitle}` : '',
        '用户指令可能是改写指令或提问：若是改写，**只输出改写后的纯文本**，不加任何解释、标题、引号、Markdown 格式或前后缀，保持与原文相近的段落数量；若是提问，用 Markdown 简洁作答，不要主动改写原文。',
        '参考资料仅供背景参考，不要原文复述。',
      ]
        .filter(Boolean)
        .join('\n')

      // 关联章节/大纲仍在客户端拼装；记忆/伏笔改走后端统一长期记忆编排。
      const injectedContext = await buildInjectedContext({
        bookId,
        contextWindow,
        currentChapter: chapterId
          ? { id: chapterId, title: chapterTitle, text: getCurrentChapterText() }
          : null,
        selectionFlat: { start: capture.flatStart, end: capture.flatEnd },
        selectionText: capture.text,
        instruction: instr,
        associatedChapterIds,
        associatedOutlineIds,
        availableOutlines,
        chapterSelectOptions,
        selectedLongTermMemoryIds,
        selectedMemoryIds,
        selectedForeshadowingIds,
      })

      const userPromptParts: string[] = []
      if (injectedContext) {
        userPromptParts.push('【参考资料（请仅作背景参考，不要原文复述）】')
        userPromptParts.push(injectedContext)
        userPromptParts.push('')
      }
      userPromptParts.push(`【指令】${instr}`)
      userPromptParts.push('')
      userPromptParts.push('【选中文段】')
      userPromptParts.push(capture.text)
      userPromptParts.push('')
      userPromptParts.push('请按指令处理上面的选中文段（改写则直接输出改写后的文本，提问则直接作答）：')
      const userPrompt = userPromptParts.join('\n')

      services.ai.aiChatStream({
        streamId,
        apiKey: model.apiKey,
        baseURL: model.baseUrl || undefined,
        apiProvider: normalizeApiProvider(model.apiProvider),
        messages: [
          { role: 'system', content: finalSystem },
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
      capture.flatEnd,
      capture.flatStart,
      capture.text,
      chapterId,
      chapterTitle,
      chapterSelectOptions,
      cleanupStream,
      getCurrentChapterText,
      model,
      selectedLongTermMemoryIds,
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

  // 校验式落盘共用：失败时保留结果并提示，用户可手动复制
  const applyResult = (applied: InlineApplyResult, source: string) => {
    if (applied === 'stale') {
      setError('原文已变化，无法定位原选区；结果已保留，可手动复制。')
      return false
    }
    window.dispatchEvent(new CustomEvent('inline-edit-accepted', {
      detail: { chapterId, source },
    }))
    onClose()
    return true
  }

  // 替换：净化 Markdown 装饰后替换选区
  const handleAccept = () => {
    const clean = normalizeGeneratedPlainText(result)
    if (!clean) return
    applyResult(capture.replace(clean), 'inline_edit')
  }

  // 插入：结果净化后作为新段落插到选区之后，不改动原文
  const handleInsertAfter = () => {
    const clean = normalizeGeneratedPlainText(result)
    if (!clean) return
    applyResult(capture.insertAfter(clean), 'inline_ask')
  }

  const handleRegenerate = () => {
    submit(instruction)
  }

  // 键盘：Esc 关闭；Ctrl/Cmd+Enter 提交；Ctrl/Cmd+Shift+Enter 替换选中
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
        <span>Inline 助手</span>
        <PurrButton
          type="text"
          size="small"
          icon={<CloseIcon style={{ fontSize: 14 }} />}
          onClick={onClose}
        />
      </div>

      <div className="inline-edit-popover-body">
        <div className="inline-edit-popover-origin" title="选中文本">
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
            selectedLongTermMemoryIds={selectedLongTermMemoryIds}
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

        <div className="inline-edit-popover-presets">
          {PRESETS.map((p) => (
            <PurrTooltip key={p.id} title={p.prompt}>
              <PurrButton
                type="text"
                size="small"
                className={
                  'inline-edit-popover-preset-btn' +
                  (instruction === p.prompt ? ' is-active' : '')
                }
                disabled={loading}
                onClick={() => setInstruction(p.prompt)}
              >
                {p.label}
              </PurrButton>
            </PurrTooltip>
          ))}
        </div>

        <div className="inline-edit-popover-input-wrap">
          <textarea
            ref={textareaRef}
            className="inline-edit-popover-textarea"
            placeholder="改写指令或提问，也可点上方预设... (Ctrl+Enter 生成)"
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
                  {onSaveAnnotation && (
                    <PurrButton
                      type="text"
                      size="small"
                      icon={<HighlightIcon size={15} />}
                      onClick={() => onSaveAnnotation(result)}
                    >
                      存为批注
                    </PurrButton>
                  )}
                  <PurrButton
                    type="text"
                    size="small"
                    icon={<ImportIcon size={15} />}
                    onClick={handleInsertAfter}
                  >
                    插入正文
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
