/**
 * 编辑器选中文字 → 浮出 Inline Edit 工具条 → 打开 Popover 输入改写指令 → AI 流式生成 → 替换/追加/取消。
 *
 * 架构：
 * - `InlineEditLayer` 对外唯一导出。EditorPanel 通过 props 传入 Lexical ref + 模型配置 + 上下文。
 * - 由 LexicalEditor 的 `onSelectionChange` 喂入 selection rect + text，触发 `SelectionBubble`。
 * - 点击 bubble 的按钮：调用 `captureSelection()` 拿到 `{ text, restore, replace }` 快照，
 *   随后打开 `InlineEditPopover` 进行 AI 流式改写；用户按「替换」时调 `replace(newText)` 落盘。
 * - 快照机制保证了：popover 打开后即使编辑器失焦，原选区仍可被恢复与替换。
 *
 * 上下文集成：
 * - Layer 持有「关联章节/大纲」「记忆/伏笔」选区，与主 AI 面板通过 localStorage 共享同一份默认勾选；
 * - Popover 顶部嵌入 `AiContextBar`：关联（章节/大纲）/ 注入（设定/伏笔）/ 提示词模板；
 * - 提交时关联章节/大纲全文与记忆/伏笔条目会作为 [参考资料] 拼入 user prompt。
 */

import React from 'react'
import { Button, Tooltip, Select, Switch, Divider } from 'antd'
import { BulbOutlined, CloseOutlined, CheckOutlined, RedoOutlined } from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import StopCircleIcon from '../../icons/StopCircleIcon'
import type { AiModelConfig, AiSparkIdea, AiForeshadowing, EntityId, Outline } from '../../types'
import type { LexicalEditorHandle } from './LexicalEditor'
import AiContextBar from '../AiPanel/components/AiContextBar'
import MemoryModal from '../AiPanel/components/MemoryModal'
import { useAssociatedContext, useMemorySelection } from '../AiPanel/hooks'
import type { PromptTemplateContext } from '../AiPanel/promptTemplates'
import './InlineEditLayer.scss'

// ── 预设改写指令 ──────────────────────────────────────────────────
const PRESETS: { id: string; label: string; prompt: string }[] = [
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

/** 单条关联章节的内容截断阈值，避免 prompt 爆炸 */
const ASSOCIATED_CHAPTER_CHAR_LIMIT = 2000

interface InlineEditLayerProps {
  /** Lexical 编辑器 ref，用于捕获/替换选区 */
  lexicalRef: React.RefObject<LexicalEditorHandle | null>
  /** 选区状态由 LexicalEditor 的 onSelectionChange 注入 */
  selection: { text: string; rect: DOMRect } | null
  /** 外部清空选区状态（点击关闭后） */
  onClearSelection: () => void
  modelConfigs: AiModelConfig[]
  selectedModelId: string
  /** 用户在 popover 内切换模型时回调，让外层持久化（与 ai-floating 共享一份选择） */
  onSelectedModelChange?: (id: string) => void
  thinkingEnabled?: boolean
  onThinkingChange?: (v: boolean) => void
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  /** 章节概要 / 风格 / 书名，用于给 AI system prompt */
  bookTitle?: string
  /** 写作章节列表（用于关联章节下拉） */
  writingChapters: { id: EntityId; title: string }[]
}

interface InlineCapture {
  text: string
  restore: () => void
  replace: (newText: string) => void
}

export default function InlineEditLayer({
  lexicalRef,
  selection,
  onClearSelection,
  modelConfigs,
  selectedModelId,
  onSelectedModelChange,
  thinkingEnabled = false,
  onThinkingChange,
  bookId,
  chapterId,
  chapterTitle,
  bookTitle,
  writingChapters,
}: InlineEditLayerProps) {
  // 被点击过 preset 后，持有选区快照并进入 popover 模式。
  const [capture, setCapture] = React.useState<InlineCapture | null>(null)
  const [initialPrompt, setInitialPrompt] = React.useState('')

  // 若外部没传 onSelectedModelChange / onThinkingChange，本地兜底 state 让 popover 仍可切换
  const [localModelId, setLocalModelId] = React.useState(selectedModelId)
  const [localThinking, setLocalThinking] = React.useState(thinkingEnabled)
  React.useEffect(() => {
    setLocalModelId(selectedModelId)
  }, [selectedModelId])
  React.useEffect(() => {
    setLocalThinking(thinkingEnabled)
  }, [thinkingEnabled])
  const effectiveModelId = onSelectedModelChange ? selectedModelId : localModelId
  const effectiveThinking = onThinkingChange ? thinkingEnabled : localThinking
  const setModelId = onSelectedModelChange ?? setLocalModelId
  const setThinking = onThinkingChange ?? setLocalThinking

  const activeModel = React.useMemo(
    () => modelConfigs.find((m) => m.id === effectiveModelId) ?? modelConfigs[0],
    [modelConfigs, effectiveModelId]
  )

  // 关联章节 / 大纲（与主 AI 面板共享一份持久化选择）
  const {
    associatedChapterIds,
    setAssociatedChapterIds,
    associatedOutlineIds,
    setAssociatedOutlineIds,
    availableOutlines,
    chapterSelectOptions,
    outlineSelectOptions,
    handleQuickAssociateChapter,
    handleQuickAssociateOutline,
  } = useAssociatedContext({ bookId, chapterId, writingChapters })

  // 记忆 / 伏笔（同样跨入口共享）
  const {
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId)

  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false)

  const handleBubbleTrigger = React.useCallback(
    (presetPrompt: string) => {
      const snap = lexicalRef.current?.captureSelection()
      if (!snap) return
      setCapture(snap)
      setInitialPrompt(presetPrompt)
    },
    [lexicalRef]
  )

  const handleClosePopover = React.useCallback(() => {
    setCapture(null)
    setInitialPrompt('')
    onClearSelection()
  }, [onClearSelection])

  return (
    <>
      {selection && !capture && (
        <SelectionBubble
          rect={selection.rect}
          onPreset={(p) => handleBubbleTrigger(p.prompt)}
          onCustom={() => handleBubbleTrigger('')}
        />
      )}
      {capture && activeModel && (
        <InlineEditPopover
          capture={capture}
          initialPrompt={initialPrompt}
          modelConfigs={modelConfigs}
          selectedModelId={effectiveModelId}
          onSelectedModelChange={setModelId}
          thinkingEnabled={effectiveThinking}
          onThinkingChange={setThinking}
          bookId={bookId}
          chapterId={chapterId}
          chapterTitle={chapterTitle}
          bookTitle={bookTitle}
          onClose={handleClosePopover}
          // 上下文相关
          associatedChapterIds={associatedChapterIds}
          setAssociatedChapterIds={setAssociatedChapterIds}
          associatedOutlineIds={associatedOutlineIds}
          setAssociatedOutlineIds={setAssociatedOutlineIds}
          availableOutlines={availableOutlines}
          chapterSelectOptions={chapterSelectOptions}
          outlineSelectOptions={outlineSelectOptions}
          handleQuickAssociateChapter={handleQuickAssociateChapter}
          handleQuickAssociateOutline={handleQuickAssociateOutline}
          selectedMemoryIds={selectedMemoryIds}
          selectedForeshadowingIds={selectedForeshadowingIds}
          onOpenMemoryModal={() => setMemoryModalOpen(true)}
        />
      )}
      <MemoryModal
        open={memoryModalOpen}
        onCancel={() => setMemoryModalOpen(false)}
        bookId={bookId}
        writingChapters={writingChapters}
        selectedIds={selectedMemoryIds}
        selectedForeshadowingIds={selectedForeshadowingIds}
        onSelectConfirm={(memoryIds, foreshadowingIds) => {
          setSelectedMemoryIds(memoryIds)
          setSelectedForeshadowingIds(foreshadowingIds)
        }}
      />
    </>
  )
}

// ─── SelectionBubble ──────────────────────────────────────────────
function SelectionBubble({
  rect,
  onPreset,
  onCustom,
}: {
  rect: DOMRect
  onPreset: (p: (typeof PRESETS)[number]) => void
  onCustom: () => void
}) {
  // 位于选区上方居中，若上方无空间则翻转到下方
  const style = React.useMemo<React.CSSProperties>(() => {
    const GAP = 8
    const BUBBLE_HEIGHT = 36
    const top =
      rect.top - BUBBLE_HEIGHT - GAP > 8
        ? rect.top - BUBBLE_HEIGHT - GAP
        : rect.bottom + GAP
    const centerX = rect.left + rect.width / 2
    return {
      position: 'fixed',
      top,
      left: centerX,
      transform: 'translateX(-50%)',
    }
  }, [rect])

  return (
    <div
      className="inline-edit-toolbar"
      style={style}
      onMouseDown={(e) => {
        // 阻止 mousedown 抢走 editor 焦点，否则会导致选区闪失。
        e.preventDefault()
      }}
    >
      {PRESETS.map((p) => (
        <Tooltip key={p.id} title={p.prompt}>
          <Button
            type="text"
            size="small"
            className="inline-edit-toolbar-btn"
            onClick={() => onPreset(p)}
          >
            {p.label}
          </Button>
        </Tooltip>
      ))}
      <div className="inline-edit-toolbar-sep" />
      <Tooltip title="自定义指令改写">
        <Button
          type="text"
          size="small"
          className="inline-edit-toolbar-btn"
          icon={<BulbOutlined />}
          onClick={onCustom}
        >
          自定义
        </Button>
      </Tooltip>
    </div>
  )
}

// ─── InlineEditPopover ─────────────────────────────────────────────
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

function InlineEditPopover({
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

  /** 拉取关联章节的正文与关联大纲的 markdown，以及记忆/伏笔条目，用于拼入 user prompt */
  const fetchInjectedContext = React.useCallback(async (): Promise<string> => {
    const blocks: string[] = []

    // 关联章节正文
    if (associatedChapterIds.length > 0) {
      const fetched = await Promise.all(
        associatedChapterIds.map(async (id) => {
          const titleOpt = chapterSelectOptions.find(
            (o) => String(o.value) === String(id),
          )
          const title = titleOpt?.label || `章节 ${id}`
          try {
            const res = await window.electronAPI.getArticle({ chapterId: id })
            const content = res.success ? res.data?.content?.trim() ?? '' : ''
            const truncated =
              content.length > ASSOCIATED_CHAPTER_CHAR_LIMIT
                ? content.slice(0, ASSOCIATED_CHAPTER_CHAR_LIMIT) +
                  `\n……（已截断，原文约 ${content.length} 字）`
                : content
            return truncated
              ? `《${title}》\n${truncated}`
              : `《${title}》（暂无正文）`
          } catch {
            return `《${title}》（读取失败）`
          }
        }),
      )
      blocks.push(`【关联章节正文】\n${fetched.join('\n\n———\n\n')}`)
    }

    // 关联大纲（直接复用 availableOutlines 中的 markdown_content）
    if (associatedOutlineIds.length > 0) {
      const lines = associatedOutlineIds.map((oid) => {
        const o = availableOutlines.find(
          (x) => String(x.id) === String(oid),
        )
        if (!o) return ''
        const md = (o.markdown_content || '').trim()
        return md
          ? `《${o.title}》\n${md}`
          : `《${o.title}》（暂无大纲文本）`
      })
      const joined = lines.filter(Boolean).join('\n\n———\n\n')
      if (joined) blocks.push(`【关联大纲】\n${joined}`)
    }

    // 记忆条目
    if (selectedMemoryIds.length > 0) {
      try {
        const res = await window.electronAPI.getSparkIdeasByIds({
          ids: selectedMemoryIds,
        })
        if (res.success && res.data && res.data.length > 0) {
          const lines = (res.data as AiSparkIdea[]).map(
            (m) => `- [${m.layer}] ${m.content}`,
          )
          blocks.push(`【本书设定（参考，请勿与人物/世界观冲突）】\n${lines.join('\n')}`)
        }
      } catch {
        // ignore
      }
    }

    // 伏笔条目
    if (selectedForeshadowingIds.length > 0) {
      try {
        const res = await window.electronAPI.getForeshadowingByIds({
          ids: selectedForeshadowingIds,
        })
        if (res.success && res.data && res.data.length > 0) {
          const lines = (res.data as AiForeshadowing[]).map(
            (f) => `- [${f.type}|${f.status}] ${f.content}`,
          )
          blocks.push(`【伏笔（参考，可顺势呼应或铺垫）】\n${lines.join('\n')}`)
        }
      } catch {
        // ignore
      }
    }

    return blocks.join('\n\n')
  }, [
    associatedChapterIds,
    associatedOutlineIds,
    availableOutlines,
    chapterSelectOptions,
    selectedForeshadowingIds,
    selectedMemoryIds,
  ])

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

      // 关联与注入：客户端拼装到 user prompt（chatAgentMode='ask' 不会在后端注入）
      const injectedContext = await fetchInjectedContext()

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
        agentMode: 'legacy',
        chatAgentMode: 'ask',
      })
    },
    [
      bookId,
      bookTitle,
      capture.text,
      chapterId,
      chapterTitle,
      cleanupStream,
      fetchInjectedContext,
      model,
      thinkingEnabled,
      thinkingOnly,
    ],
  )

  const handleAbort = () => {
    cleanupStream()
    window.electronAPI.abortAiStream?.()
    setLoading(false)
  }

  const handleAccept = () => {
    const clean = result.trim()
    if (!clean) return
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
