import type { ProposedSettingDiff } from '../../types.ts'

export function createSettingDiffOccurrenceQueue() {
  const bySessionKey = new Map<string, ProposedSettingDiff[]>()

  return {
    enqueue(sessionKey: string, proposal: ProposedSettingDiff): boolean {
      const proposalId = String(proposal.proposalId || '').trim()
      if (!proposalId) return false
      const current = bySessionKey.get(sessionKey) ?? []
      if (!current.some((item) => item.proposalId === proposalId)) {
        bySessionKey.set(sessionKey, [...current, proposal])
      }
      return true
    },
    shift(sessionKey: string): ProposedSettingDiff | undefined {
      const current = bySessionKey.get(sessionKey)
      if (!current?.length) return undefined
      const [next, ...rest] = current
      if (rest.length) bySessionKey.set(sessionKey, rest)
      else bySessionKey.delete(sessionKey)
      return next
    },
    isQueued(proposalId: string): boolean {
      for (const proposals of bySessionKey.values()) {
        if (proposals.some((proposal) => proposal.proposalId === proposalId)) {
          return true
        }
      }
      return false
    },
    evictWhere(predicate: (proposal: ProposedSettingDiff) => boolean): string[] {
      const affected: string[] = []
      for (const [sessionKey, proposals] of bySessionKey) {
        const retained = proposals.filter((proposal) => !predicate(proposal))
        if (retained.length === proposals.length) continue
        affected.push(sessionKey)
        if (retained.length) bySessionKey.set(sessionKey, retained)
        else bySessionKey.delete(sessionKey)
      }
      return affected
    },
    clear(): void {
      bySessionKey.clear()
    },
  }
}

/** Synchronous per-entity command exclusion; React state only mirrors it. */
export function createSettingDiffCommandLatch() {
  const pending = new Map<string, string>()
  return {
    tryBegin(sessionKey: string, proposalId: string): boolean {
      if (pending.has(sessionKey)) return false
      pending.set(sessionKey, proposalId)
      return true
    },
    canMutate(sessionKey: string, proposalId: string): boolean {
      return pending.get(sessionKey) !== proposalId
    },
    canComplete(
      sessionKey: string,
      proposalId: string,
      currentProposalId: string | undefined,
    ): boolean {
      return pending.get(sessionKey) === proposalId
        && currentProposalId === proposalId
    },
    release(sessionKey: string, proposalId: string): void {
      if (pending.get(sessionKey) === proposalId) pending.delete(sessionKey)
    },
    evictProposal(proposalId: string): void {
      for (const [sessionKey, pendingProposalId] of pending) {
        if (pendingProposalId === proposalId) pending.delete(sessionKey)
      }
    },
    clear(): void {
      pending.clear()
    },
  }
}
