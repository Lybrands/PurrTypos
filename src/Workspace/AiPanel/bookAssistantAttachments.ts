import type { AgentConversationMessage } from '../../agent-runtime/contracts.ts'
import type { SettingDiffCardState } from '../../types.ts'

export type BookAssistantAttachmentStore = Record<
  string,
  SettingDiffCardState[]
>

const EMPTY_ATTACHMENTS: SettingDiffCardState[] = []

export function bookAttachmentKeys(
  message: AgentConversationMessage,
): string[] {
  const keys: string[] = []
  if (message.clientTurnId) keys.push(`client:${message.clientTurnId}`)
  if (message.conversationId != null) {
    keys.push(`conversation:${message.conversationId}`)
  }
  if (message.agentRunId) keys.push(`run:${message.agentRunId}`)
  return keys
}

export function bookAttachmentKey(
  message: AgentConversationMessage,
): string | undefined {
  return bookAttachmentKeys(message)[0]
}

export function getBookAssistantAttachmentsForMessage(
  store: BookAssistantAttachmentStore,
  message: AgentConversationMessage,
): SettingDiffCardState[] {
  for (const key of bookAttachmentKeys(message)) {
    const cards = store[key]
    if (cards?.length) return cards
  }
  return EMPTY_ATTACHMENTS
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
  const keys = bookAttachmentKeys(message)
  if (keys.length === 0) return false
  const next = keys.reduce(
    (store, key) => reduceBookAssistantAttachment(store, key, card),
    attachmentStore,
  )
  return replaceBookAssistantAttachments(next)
}

export function associateBookAssistantAttachmentIdentities(
  source: AgentConversationMessage,
  target: AgentConversationMessage,
): boolean {
  const keys = [...new Set([
    ...bookAttachmentKeys(source),
    ...bookAttachmentKeys(target),
  ])]
  const cardsBySession = new Map<string, SettingDiffCardState>()
  for (const key of keys) {
    for (const card of attachmentStore[key] ?? []) {
      cardsBySession.set(card.sessionKey, card)
    }
  }
  if (cardsBySession.size === 0) return false
  const cards = [...cardsBySession.values()]
  return replaceBookAssistantAttachments(Object.fromEntries([
    ...Object.entries(attachmentStore),
    ...keys.map((key) => [key, cards] as const),
  ]))
}

export function resolveStoredBookAssistantAttachments(
  detail: SettingDiffCardState,
): boolean {
  return replaceBookAssistantAttachments(
    resolveBookAssistantAttachments(attachmentStore, detail),
  )
}
