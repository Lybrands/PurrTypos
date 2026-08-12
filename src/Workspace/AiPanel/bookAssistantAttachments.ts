import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import type { SettingDiffCardState } from '../../types.ts'

export type BookAssistantAttachmentStore = Record<
  string,
  SettingDiffCardState[]
>

export function bookAttachmentKey(
  message: AgentConversationMessage,
): string | undefined {
  if (message.clientTurnId) return `client:${message.clientTurnId}`
  if (message.conversationId != null) return `conversation:${message.conversationId}`
  if (message.agentRunId) return `run:${message.agentRunId}`
  return undefined
}

export function reduceBookAssistantAttachment(
  store: BookAssistantAttachmentStore,
  key: string | undefined,
  card: SettingDiffCardState,
): BookAssistantAttachmentStore {
  if (!key) return store
  const current = store[key] ?? []
  const existingIndex = current.findIndex(
    (entry) => entry.sessionKey === card.sessionKey,
  )
  const cards = [...current]
  if (existingIndex >= 0) cards[existingIndex] = card
  else cards.push(card)
  return { ...store, [key]: cards }
}

export function resolveBookAssistantAttachments(
  store: BookAssistantAttachmentStore,
  detail: SettingDiffCardState,
): BookAssistantAttachmentStore {
  let changed = false
  const next = Object.fromEntries(
    Object.entries(store).map(([key, cards]) => [
      key,
      cards.map((card) => {
        if (card.sessionKey !== detail.sessionKey) return card
        changed = true
        return { ...card, ...detail }
      }),
    ]),
  )
  return changed ? next : store
}

let attachmentStore: BookAssistantAttachmentStore = {}
let attachmentVersion = 0
const attachmentListeners = new Set<() => void>()

function replaceBookAssistantAttachments(
  next: BookAssistantAttachmentStore,
): boolean {
  if (next === attachmentStore) return false
  attachmentStore = next
  attachmentVersion += 1
  attachmentListeners.forEach((listener) => listener())
  return true
}

export function getBookAssistantAttachments(): BookAssistantAttachmentStore {
  return attachmentStore
}

export function getBookAssistantAttachmentsVersion(): number {
  return attachmentVersion
}

export function subscribeBookAssistantAttachments(listener: () => void): () => void {
  attachmentListeners.add(listener)
  return () => attachmentListeners.delete(listener)
}

export function addBookAssistantAttachment(
  message: AgentConversationMessage,
  card: SettingDiffCardState,
): boolean {
  const key = bookAttachmentKey(message)
  if (!key) return false
  return replaceBookAssistantAttachments(
    reduceBookAssistantAttachment(attachmentStore, key, card),
  )
}

export function resolveStoredBookAssistantAttachments(
  detail: SettingDiffCardState,
): boolean {
  return replaceBookAssistantAttachments(
    resolveBookAssistantAttachments(attachmentStore, detail),
  )
}
