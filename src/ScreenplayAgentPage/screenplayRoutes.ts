export type ScreenplayRoute =
  | { kind: 'projects' }
  | { kind: 'new'; bookId: string | null }
  | {
      kind: 'project'
      projectId: string
    }
  | { kind: 'invalid' }

export function parseScreenplayRoute(pathname: string, search = ''): ScreenplayRoute {
  const parts = pathname.split('/').filter(Boolean)
  if (parts.length === 1 && parts[0] === 'screenplay') return { kind: 'projects' }
  if (parts.length === 2 && parts[0] === 'screenplay' && parts[1] === 'new') {
    return {
      kind: 'new',
      bookId: new URLSearchParams(search).get('bookId')?.trim() || null,
    }
  }
  if (
    parts[0] !== 'screenplay'
    || parts[1] !== 'projects'
    || parts.length !== 3
  ) return { kind: 'invalid' }
  try {
    const projectId = decodeURIComponent(parts[2])
    if (!projectId) return { kind: 'invalid' }
    return {
      kind: 'project',
      projectId,
    }
  } catch {
    return { kind: 'invalid' }
  }
}

export function screenplayNewPath(bookId?: string | null): string {
  if (!bookId) return '/screenplay/new'
  return `/screenplay/new?${new URLSearchParams({ bookId })}`
}

export function screenplayProjectPath(projectId: string): string {
  return `/screenplay/projects/${encodeURIComponent(projectId)}`
}
