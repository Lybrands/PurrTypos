/**
 * 编辑器选中文字 → 浮出 Inline 工具条 → 按动作分流：
 * - 润色/精简/扩写/自定义 → InlineEditPopover（rewrite 模式）：AI 流式生成 → 校验式替换选区
 * - 提问 → InlineEditPopover（ask 模式）：回答不落正文，可「存为批注 / 插入正文」
 * - 批注 → AnnotationComposerPopover：手动批注，锚定选区
 * - 引用 → 把选区写入 quoteStore（AI 面板输入框上方胶囊展示，可多条）
 *
 * 架构：
 * - `InlineEditLayer` 对外唯一导出。EditorPanel 通过 props 传入 Lexical ref + 模型配置 + 上下文。
 * - 由 LexicalEditor 的 `onSelectionChange` 喂入 selection rect + text，触发 `SelectionBubble`。
 * - 点击 bubble 的按钮：调用 `captureSelection()` 拿到 `{ text, flatStart/End, restore, replace }` 快照，
 *   随后打开对应弹层；落盘动作经快照校验（原文变动时按引文重定位，见 EditorHandlePlugin）。
 * - 快照机制保证了：popover 打开后即使编辑器失焦，原选区仍可被恢复与替换。
 *
 * 上下文集成：
 * - Layer 持有「关联章节/大纲」「记忆/伏笔」选区，与主 AI 面板通过 localStorage 共享同一份默认勾选；
 * - PurrPopover 顶部嵌入 `AiContextBar`：关联（章节/大纲）/ 注入（设定/伏笔）/ 提示词模板；
 * - 提交时当前章节全文与关联章节/大纲、记忆/伏笔条目作为 [参考资料] 拼入 user prompt（见 inlineEditContext）。
 */

import React from 'react'
import type { AiModelConfig, EntityId } from '../../types'
import type { LexicalEditorHandle } from './LexicalEditor'
import MemoryModal from '../AiPanel/components/MemoryModal'
import { useAssociatedContext, useMemorySelection } from '../AiPanel/hooks'
import SelectionBubble from './SelectionBubble'
import InlineEditPopover, { type InlineCapture } from './InlineEditPopover'
import AnnotationComposerPopover, { type AnnotationDraft } from './AnnotationComposerPopover'
import { addSelectionQuote } from '../../stores/quoteStore'
import './InlineEditLayer.scss'

interface InlineEditLayerProps {
  /** Lexical 编辑器 ref，用于捕获/替换选区 */
  lexicalRef: React.RefObject<LexicalEditorHandle | null>
  /** 选区状态由 LexicalEditor 的 onSelectionChange 注入 */
  selection: { text: string; rect: DOMRect } | null
  /** 外部清空选区状态（点击关闭后） */
  onClearSelection: () => void
  modelConfigs: AiModelConfig[]
  onUpdateModelConfig?: (id: string, patch: Partial<Pick<AiModelConfig, 'contextWindow' | 'thinkingEnabled' | 'reasoningEffort'>>) => void
  selectedModelId: string
  /** 用户在 popover 内切换模型时回调，让外层持久化（与 ai-floating 共享一份选择） */
  onSelectedModelChange?: (id: string) => void
  bookId: EntityId | null
  chapterId: EntityId | null
  chapterTitle: string
  /** 章节概要 / 风格 / 书名，用于给 AI system prompt */
  bookTitle?: string
  /** 写作章节列表（用于关联章节下拉） */
  writingChapters: { id: EntityId; title: string }[]
  /** 结果落盘成功后通知外层（标记自动保存 source） */
  onInlineApplied?: (source: string) => void
}

export default function InlineEditLayer({
  lexicalRef,
  selection,
  onClearSelection,
  modelConfigs,
  onUpdateModelConfig,
  selectedModelId,
  onSelectedModelChange,
  bookId,
  chapterId,
  chapterTitle,
  bookTitle,
  writingChapters,
  onInlineApplied,
}: InlineEditLayerProps) {
  // Inline 弹层（改写/提问统一入口）：持有选区快照
  const [capture, setCapture] = React.useState<InlineCapture | null>(null)
  const [initialPrompt, setInitialPrompt] = React.useState('')
  // 批注弹层：手动批注或 AI 回答转批注
  const [annotationDraft, setAnnotationDraft] = React.useState<AnnotationDraft | null>(null)

  // 若外部没传回调，本地兜底 state 让 popover 仍可切换
  const [localModelId, setLocalModelId] = React.useState(selectedModelId)
  React.useEffect(() => {
    setLocalModelId(selectedModelId)
  }, [selectedModelId])
  const effectiveModelId = onSelectedModelChange ? selectedModelId : localModelId
  const setModelId = onSelectedModelChange ?? setLocalModelId

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
    selectedLongTermMemoryIds,
    setSelectedLongTermMemoryIds,
    selectedMemoryIds,
    setSelectedMemoryIds,
    selectedForeshadowingIds,
    setSelectedForeshadowingIds,
  } = useMemorySelection(bookId)

  const [memoryModalOpen, setMemoryModalOpen] = React.useState(false)

  const getFlatText = React.useCallback(() => lexicalRef.current?.getFlatText() ?? '', [lexicalRef])

  const openInlinePopover = React.useCallback(
    (prompt: string) => {
      const snap = lexicalRef.current?.captureSelection()
      if (!snap) return
      setCapture(snap)
      setInitialPrompt(prompt)
    },
    [lexicalRef]
  )

  const handleAnnotate = React.useCallback(() => {
    const snap = lexicalRef.current?.captureSelection()
    if (!snap) return
    setAnnotationDraft({ capture: snap, initialNote: '', source: 'manual' })
  }, [lexicalRef])

  const handleQuote = React.useCallback(() => {
    const snap = lexicalRef.current?.captureSelection()
    if (!snap) return
    addSelectionQuote(snap.text, chapterTitle)
    onClearSelection()
  }, [lexicalRef, chapterTitle, onClearSelection])

  const handleClosePopover = React.useCallback(() => {
    setCapture(null)
    setInitialPrompt('')
    onClearSelection()
  }, [onClearSelection])

  // ask 模式「存为批注」：关掉提问弹层，锚点沿用，AI 回答预填批注
  const handleSaveAnnotation = React.useCallback(
    (answer: string) => {
      if (!capture) return
      setAnnotationDraft({ capture, initialNote: answer, source: 'ai' })
      setCapture(null)
      setInitialPrompt('')
    },
    [capture],
  )

  const handleCloseAnnotation = React.useCallback(() => {
    setAnnotationDraft(null)
    onClearSelection()
  }, [onClearSelection])

  return (
    <>
      {selection && !capture && !annotationDraft && (
        <SelectionBubble
          rect={selection.rect}
          onAi={() => openInlinePopover('')}
          onAnnotate={handleAnnotate}
          onQuote={handleQuote}
        />
      )}
      {capture && activeModel && (
        <InlineEditPopover
          capture={capture}
          initialPrompt={initialPrompt}
          modelConfigs={modelConfigs}
          onUpdateModelConfig={onUpdateModelConfig}
          selectedModelId={effectiveModelId}
          onSelectedModelChange={setModelId}
          bookId={bookId}
          chapterId={chapterId}
          chapterTitle={chapterTitle}
          bookTitle={bookTitle}
          getCurrentChapterText={getFlatText}
          onClose={handleClosePopover}
          onSaveAnnotation={handleSaveAnnotation}
          onApplied={onInlineApplied}
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
          selectedLongTermMemoryIds={selectedLongTermMemoryIds}
          selectedMemoryIds={selectedMemoryIds}
          selectedForeshadowingIds={selectedForeshadowingIds}
          onOpenMemoryModal={() => setMemoryModalOpen(true)}
        />
      )}
      {annotationDraft && (
        <AnnotationComposerPopover
          bookId={bookId}
          chapterId={chapterId}
          draft={annotationDraft}
          getFlatText={getFlatText}
          onClose={handleCloseAnnotation}
        />
      )}
      <MemoryModal
        open={memoryModalOpen}
        onCancel={() => setMemoryModalOpen(false)}
        bookId={bookId}
        writingChapters={writingChapters}
        selectedLongTermMemoryIds={selectedLongTermMemoryIds}
        selectedIds={selectedMemoryIds}
        selectedForeshadowingIds={selectedForeshadowingIds}
        onSelectConfirm={(longTermMemoryIds, memoryIds, foreshadowingIds) => {
          setSelectedLongTermMemoryIds(longTermMemoryIds)
          setSelectedMemoryIds(memoryIds)
          setSelectedForeshadowingIds(foreshadowingIds)
        }}
      />
    </>
  )
}
