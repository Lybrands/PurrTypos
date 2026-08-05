/** Normalize persisted SQLite UTC timestamps for conversation timing. */
export function parseLongTaskTime(value: string | null | undefined): number | null {
  if (!value) return null
  const isoLike = value.replace(' ', 'T')
  // SQLite CURRENT_TIMESTAMP is UTC. FastAPI may serialize the same naive
  // value either with a space or with T, so the presence of T is not timezone
  // information. Only preserve the value when it has an explicit zone.
  const normalized = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(isoLike)
    ? isoLike
    : `${isoLike}Z`
  const parsed = Date.parse(normalized)
  return Number.isFinite(parsed) ? parsed : null
}
