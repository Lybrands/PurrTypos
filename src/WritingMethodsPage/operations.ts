import type { ApiResult } from '../types'
import type { TechniqueFileChange } from '../services/writingTechniques'

export function requireTechniqueData<T>(result: ApiResult<T>): T {
  if (!result.success || result.data == null) throw new Error(result.error || '写作技法操作失败')
  return result.data
}

export function putTechniqueFile(changes: TechniqueFileChange[], path: string, content: string): TechniqueFileChange[] {
  const next = [...changes]
  for (let index = next.length - 1; index >= 0; index--) {
    const change = next[index]
    if (change.path === path || change.target === path) {
      if (change.action === 'put') { next[index] = { action: 'put', path, content }; return next }
      break
    }
  }
  return [...next, { action: 'put', path, content }]
}
