import React from 'react'
import {
  PurrButton,
  PurrSegmented,
  PurrSelect,
  PurrTooltip,
  ReadIcon,
  StarIcon,
} from '@/purr-components'
import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import type { AgentConversationExtensions } from '../../components/AgentConversation/extensions.ts'
import { getAssistantRenderableMarkdown } from '../../components/AgentConversation/assistantCopy.ts'
import type {
  ChatAgentMode,
  EntityId,
  WritingMethodOverrides,
} from '../../types.ts'
import type { BoundWritingMethodChoice } from './writingMethodOverrides'
import { writingMethodOverrideMode } from './writingMethodOverrides'
import AiContextBar, {
  type AiContextBarBindings,
} from './components/AiContextBar'
import SettingDiffCard from './components/SettingDiffCard'
import type { PromptTemplateContext } from './promptTemplates'
import {
  getBookAssistantAttachmentsForMessage,
  type BookAssistantAttachmentStore,
} from './bookAssistantAttachments'
import type { ChatSessionScope } from './hooks/useAiSessions'

interface BookConversationExtensionBindings {
  bookId: EntityId | null | undefined
  bookTitle: string
  chapterId: EntityId | null | undefined
  chapterTitle?: string
  scope: ChatSessionScope
  setScope(scope: ChatSessionScope): void
  chatAgentMode: ChatAgentMode
  setChatAgentMode(mode: ChatAgentMode): void
  contextBar: AiContextBarBindings
  prompt: string
  onInsertPrompt(text: string): void
  promptTemplateContext: PromptTemplateContext
  messages: AgentConversationMessage[]
  attachments: BookAssistantAttachmentStore
  running: boolean
  onAddFavorite(prompt: string, content: string): void
  writingMethodChoices: BoundWritingMethodChoice[]
  writingMethodOverrides: WritingMethodOverrides
  onCycleWritingMethod(revisionId: string): void
  onRequestWritingMethodRecommendation(): void
}

export function useBookConversationExtensions({
  bookId,
  bookTitle,
  chapterId,
  chapterTitle,
  scope,
  setScope,
  chatAgentMode,
  setChatAgentMode,
  contextBar,
  prompt,
  onInsertPrompt,
  promptTemplateContext,
  messages,
  attachments,
  running,
  onAddFavorite,
  writingMethodChoices,
  writingMethodOverrides,
  onCycleWritingMethod,
  onRequestWritingMethodRecommendation,
}: BookConversationExtensionBindings): AgentConversationExtensions {
  return React.useMemo(() => ({
    composerCommands: writingMethodChoices.map((method) => {
      const mode = writingMethodOverrideMode(
        writingMethodOverrides,
        method.revisionId,
      )
      return {
        id: `writing-method:${method.revisionId}`,
        label: method.name,
        description: `${mode === 'force' ? '本轮强制' : mode === 'exclude' ? '本轮排除' : '按任务自动选择'} · v${method.versionNo} · ${method.source === 'scheme' ? '来自写作方案' : '直接绑定'}`,
        keywords: [method.methodType === 'primary' ? '主方法' : '专项技法'],
        active: mode !== 'default',
        onSelect: () => onCycleWritingMethod(method.revisionId),
      }
    }),
    renderSessionContext: () => (
      <>
        <div className="conversation-book-card">
          <div className="conversation-book-icon"><ReadIcon /></div>
          <div className="conversation-book-meta">
            <span className="conversation-book-label">当前书籍</span>
            <strong title={bookTitle || '未命名书籍'}>
              {bookTitle || '未命名书籍'}
            </strong>
            <span title={scope === 'chapter' ? chapterTitle : '整本书'}>
              {scope === 'chapter'
                ? (chapterTitle || '未选择章节')
                : '整本书 · 全局上下文'}
            </span>
          </div>
        </div>
        <PurrSegmented
          block
          size="small"
          value={scope}
          options={[
            { label: '章节', value: 'chapter' },
            { label: '全局', value: 'setting' },
          ]}
          onChange={(value) => setScope(value as ChatSessionScope)}
          className="conversation-scope-switch"
        />
      </>
    ),
    renderComposerLeading: () => (
      <>
        <PurrSelect
          className={`ai-agent-select ${chatAgentMode === 'agent' ? 'ai-agent-select--on' : ''}`}
          size="small"
          value={chatAgentMode}
          onChange={setChatAgentMode}
          options={[
            { value: 'agent', label: '智能体' },
            { value: 'ask', label: '问答' },
          ]}
        />
        <AiContextBar
          bookId={bookId ?? null}
          chapterId={chapterId ?? null}
          {...contextBar}
          currentPrompt={prompt}
          onInsertPrompt={onInsertPrompt}
          promptTemplateContext={promptTemplateContext}
          promptTemplateDisabled={false}
        />
        <PurrButton
          type="text"
          size="small"
          disabled={running}
          onClick={onRequestWritingMethodRecommendation}
        >
          推荐方法
        </PurrButton>
        {(writingMethodOverrides.forceRevisionIds.length > 0
          || writingMethodOverrides.excludeRevisionIds.length > 0) ? (
          <span className="writing-method-turn-status">
            本轮方法 · 强制 {writingMethodOverrides.forceRevisionIds.length}
            {' · '}排除 {writingMethodOverrides.excludeRevisionIds.length}
          </span>
        ) : null}
      </>
    ),
    renderAssistantAttachment: (message, index) => {
      const cards = message.isError
        ? []
        : getBookAssistantAttachmentsForMessage(attachments, message)
      if (cards.length === 0) return null
      return (
        <div className="book-assistant-attachments">
          {cards.map((card) => (
            <SettingDiffCard key={card.proposalId} card={card} />
          ))}
        </div>
      )
    },
    renderAssistantActions: (message, index) => {
      const previous = messages[index - 1]
      const favoriteContent = getAssistantRenderableMarkdown(message).trim()
      const canFavorite = Boolean(
        !message.isError
        && favoriteContent
        && previous?.role === 'user'
        && !(running && index === messages.length - 1),
      )
      if (!canFavorite) return null
      return (
        <PurrTooltip title="收藏">
          <PurrButton
            type="text"
            size="small"
            icon={<StarIcon style={{ fontSize: 12 }} />}
            className="agent-message-action-button"
            onClick={() => onAddFavorite(previous.content, favoriteContent)}
            aria-label="收藏回复"
          />
        </PurrTooltip>
      )
    },
  }), [
    attachments,
    bookId,
    bookTitle,
    chapterId,
    chapterTitle,
    chatAgentMode,
    contextBar,
    messages,
    onAddFavorite,
    onInsertPrompt,
    onCycleWritingMethod,
    onRequestWritingMethodRecommendation,
    prompt,
    promptTemplateContext,
    running,
    scope,
    setChatAgentMode,
    setScope,
    writingMethodChoices,
    writingMethodOverrides,
  ])
}
