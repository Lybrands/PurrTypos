import React from 'react'
import {
  PurrButton,
  PurrSegmented,
  PurrTooltip,
  ReadIcon,
  StarIcon,
} from '@/purr-components'
import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import type { AgentConversationExtensions } from '../../components/AgentConversation/extensions.ts'
import { getAssistantRenderableMarkdown } from '../../components/AgentConversation/assistantCopy.ts'
import type {
  EntityId,
} from '../../types.ts'
import type { WritingTechniqueChoice, WritingTechniqueSelection } from '../../services/writingTechniques'
import WritingTechniquePicker from './components/WritingTechniquePicker'
import WritingTechniqueUsage from './components/WritingTechniqueUsage'
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
  sessionId: number | null
  bookId: EntityId | null | undefined
  bookTitle: string
  chapterId: EntityId | null | undefined
  chapterTitle?: string
  scope: ChatSessionScope
  setScope(scope: ChatSessionScope): void
  contextBar: AiContextBarBindings
  prompt: string
  onInsertPrompt(text: string): void
  promptTemplateContext: PromptTemplateContext
  messages: AgentConversationMessage[]
  attachments: BookAssistantAttachmentStore
  running: boolean
  onAddFavorite(prompt: string, content: string): void
  writingTechniqueChoices: WritingTechniqueChoice[]
  writingTechniqueSelection: WritingTechniqueSelection
  onToggleWritingTechnique(id: string): void
  onSetWritingTechniqueMode(mode: 'manual' | 'auto'): void
}

export function useBookConversationExtensions({
  sessionId,
  bookId,
  bookTitle,
  chapterId,
  chapterTitle,
  scope,
  setScope,
  contextBar,
  prompt,
  onInsertPrompt,
  promptTemplateContext,
  messages,
  attachments,
  running,
  onAddFavorite,
  writingTechniqueChoices,
  writingTechniqueSelection,
  onToggleWritingTechnique,
  onSetWritingTechniqueMode,
}: BookConversationExtensionBindings): AgentConversationExtensions {
  return React.useMemo(() => ({
    composerCommands: writingTechniqueChoices.map(choice => ({
      id: `writing-technique:${choice.ref.id}`, label: choice.name,
      description: `${choice.ref.kind === 'scheme' ? '写作方案' : '写作技法'} · ${choice.description}`,
      keywords: ['写作技法', '写作方案'],
      active: writingTechniqueSelection.refs.some(ref => ref.id === choice.ref.id),
      onSelect: () => onToggleWritingTechnique(choice.ref.id),
    })),
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
    composerActionMenu: { triggers: ['/'], title: '对话操作', render: () => (
      <>
        <AiContextBar
          expanded
          bookId={bookId ?? null}
          chapterId={chapterId ?? null}
          {...contextBar}
          currentPrompt={prompt}
          onInsertPrompt={onInsertPrompt}
          promptTemplateContext={promptTemplateContext}
          promptTemplateDisabled={false}
        />
        <WritingTechniquePicker
          sessionId={sessionId}
          choices={writingTechniqueChoices}
          selection={writingTechniqueSelection}
          onSetMode={onSetWritingTechniqueMode}
          onToggle={onToggleWritingTechnique}
        />
      </>
    ),
    },
    renderAssistantAttachment: (message) => {
      const cards = message.isError
        ? []
        : getBookAssistantAttachmentsForMessage(attachments, message)
      return (
        <div className="book-assistant-attachments">
          <WritingTechniqueUsage runId={message.agentRunId} running={running} />
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
    sessionId,
    attachments,
    bookId,
    bookTitle,
    chapterId,
    chapterTitle,
    contextBar,
    messages,
    onAddFavorite,
    onInsertPrompt,
    onToggleWritingTechnique,
    onSetWritingTechniqueMode,
    prompt,
    promptTemplateContext,
    running,
    scope,
    setScope,
    writingTechniqueChoices,
    writingTechniqueSelection,
  ])
}
