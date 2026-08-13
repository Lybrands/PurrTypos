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
    (entry) => entry.proposalId === card.proposalId,
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
        if (card.proposalId !== detail.proposalId) return card
        changed = true
        return { ...card, ...detail }
      }),
    ]),
  )
  return changed ? next : store
}

export interface BookAssistantAttachmentManager {
  readonly ownerKey: string
  getSnapshot(): BookAssistantAttachmentStore
  getVersion(): number
  subscribe(listener: () => void): () => void
  add(
    message: AgentConversationMessage,
    card: SettingDiffCardState,
    owner?: BookAssistantAttachmentOwner,
  ): boolean
  associate(
    source: AgentConversationMessage,
    target: AgentConversationMessage,
  ): boolean
  resolve(detail: SettingDiffCardState): boolean
  evictSession(sessionId: number): boolean
  ownerForProposal(proposalId: string): BookAssistantAttachmentOwner | undefined
  productProjection(
    message: AgentConversationMessage,
  ): Record<string, unknown> | undefined
}

export interface BookAssistantAttachmentOwner {
  sessionId: number
  bookId?: string
  chapterId?: string | null
  prompt: string
  message: AgentConversationMessage
}

/** One short-lived store per Book; closed/deleted session owners are evicted. */
export function createBookAssistantAttachmentManager(
  ownerKey: string,
): BookAssistantAttachmentManager {
  let attachmentStore: BookAssistantAttachmentStore = {}
  let attachmentVersion = 0
  const attachmentListeners = new Set<() => void>()
  const owners = new Map<string, BookAssistantAttachmentOwner>()
  const evictedSessions = new Set<number>()

  const replace = (next: BookAssistantAttachmentStore): boolean => {
    if (next === attachmentStore) return false
    attachmentStore = next
    attachmentVersion += 1
    attachmentListeners.forEach((listener) => listener())
    return true
  }

  return {
    ownerKey,
    getSnapshot: () => attachmentStore,
    getVersion: () => attachmentVersion,
    subscribe(listener) {
      attachmentListeners.add(listener)
      return () => attachmentListeners.delete(listener)
    },
    add(message, card, owner) {
      if (owner && evictedSessions.has(owner.sessionId)) return false
      const keys = bookAttachmentKeys(message)
      if (keys.length === 0) return false
      if (owner) owners.set(card.proposalId, { ...owner, message })
      return replace(keys.reduce(
        (store, key) => reduceBookAssistantAttachment(store, key, card),
        attachmentStore,
      ))
    },
    associate(source, target) {
      const keys = [...new Set([
        ...bookAttachmentKeys(source),
        ...bookAttachmentKeys(target),
      ])]
      const cardsByProposal = new Map<string, SettingDiffCardState>()
      for (const key of keys) {
        for (const card of attachmentStore[key] ?? []) {
          cardsByProposal.set(card.proposalId, card)
        }
      }
      if (cardsByProposal.size === 0) return false
      for (const proposalId of cardsByProposal.keys()) {
        const owner = owners.get(proposalId)
        if (owner) owners.set(proposalId, { ...owner, message: target })
      }
      const cards = [...cardsByProposal.values()]
      return replace(Object.fromEntries([
        ...Object.entries(attachmentStore),
        ...keys.map((key) => [key, cards] as const),
      ]))
    },
    resolve(detail) {
      return replace(resolveBookAssistantAttachments(attachmentStore, detail))
    },
    evictSession(sessionId) {
      evictedSessions.add(sessionId)
      const proposalIds = new Set(
        [...owners.entries()]
          .filter(([, owner]) => owner.sessionId === sessionId)
          .map(([proposalId]) => proposalId),
      )
      if (proposalIds.size === 0) return false
      proposalIds.forEach((proposalId) => owners.delete(proposalId))
      return replace(Object.fromEntries(
        Object.entries(attachmentStore).flatMap(([key, cards]) => {
          const retained = cards.filter(
            (card) => !proposalIds.has(card.proposalId),
          )
          return retained.length ? [[key, retained]] : []
        }),
      ))
    },
    ownerForProposal: (proposalId) => owners.get(proposalId),
    productProjection(message) {
      const cards = getBookAssistantAttachmentsForMessage(
        attachmentStore,
        message,
      )
      if (cards.length === 0) return undefined
      const proposalIds = new Set(cards.map((card) => card.proposalId))
      const aliases: Record<string, Record<string, unknown[]>> = {}
      for (const [key, storedCards] of Object.entries(attachmentStore)) {
        for (const card of storedCards) {
          if (!proposalIds.has(card.proposalId)) continue
          const alias = aliases[card.proposalId] ?? {
            clientTurnIds: [],
            runIds: [],
            conversationIds: [],
          }
          if (key.startsWith('client:')) alias.clientTurnIds.push(key.slice(7))
          else if (key.startsWith('run:')) alias.runIds.push(key.slice(4))
          else if (key.startsWith('conversation:')) {
            const value = Number(key.slice(13))
            if (Number.isFinite(value)) alias.conversationIds.push(value)
          }
          aliases[card.proposalId] = alias
        }
      }
      const resolutions = Object.fromEntries(cards
        .filter((card) => card.status !== 'pending')
        .map((card) => [card.proposalId, card]))
      return {
        settingDiff: {
          version: 1,
          aliases,
          ...(Object.keys(resolutions).length ? { resolutions } : {}),
        },
      }
    },
  }
}
