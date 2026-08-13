import type {
  ProposedSettingDiff,
  SettingDiffCardState,
} from '../../types.ts'

export function settingDiffSessionKey(proposal: ProposedSettingDiff): string {
  if (proposal.kind === 'character') return `character:${proposal.characterId}`
  if (proposal.kind === 'entity') return `entity:${proposal.entityId}`
  return `background:${proposal.bookId}`
}

export function settingDiffCard(
  proposal: ProposedSettingDiff,
): SettingDiffCardState | undefined {
  const proposalId = String(proposal.proposalId || '').trim()
  if (!proposalId) return undefined
  const proposedName = proposal.kind === 'character' || proposal.kind === 'entity'
    ? (
        proposal.kind === 'character'
          ? proposal.characterName
          : proposal.entityName
      )
      || (proposal.proposed as { name?: string }).name
      || (proposal.before as { name?: string }).name
      || ''
    : ''
  const title = proposal.kind === 'character'
    ? proposedName ? `人物「${proposedName}」` : '人物设定'
    : proposal.kind === 'entity'
      ? proposedName ? `设定「${proposedName}」` : '世界设定'
      : '故事背景'
  return {
    proposalId,
    sessionKey: settingDiffSessionKey(proposal),
    kind: proposal.kind,
    title,
    status: 'pending',
  }
}
