import type { ApiResult } from '../types'
import { canRetryHttpRequest } from './httpRetryPolicy'

const configuredBaseUrl = String(import.meta.env.VITE_API_BASE_URL || '').replace(/\/+$/, '')

export const backendBaseUrl = configuredBaseUrl || (
  typeof window !== 'undefined'
    && window.location.protocol === 'http:'
    && window.location.port === '18321'
    ? window.location.origin
    : 'http://127.0.0.1:18321'
)

const retryDelay = (attempt: number) =>
  new Promise((resolve) => globalThis.setTimeout(resolve, 300 * (attempt + 1)))

export async function requestJson<T>(
  path: string,
  options: RequestInit = {},
  retries = 3,
): Promise<ApiResult<T>> {
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const response = await fetch(`${backendBaseUrl}${path}`, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...options.headers,
        },
      })
      if (
        !response.ok
        && attempt < retries
        && canRetryHttpRequest(options.method, response.status)
      ) {
        await retryDelay(attempt)
        continue
      }
      return await response.json() as ApiResult<T>
    } catch (error) {
      if (attempt >= retries || !canRetryHttpRequest(options.method)) {
        return {
          success: false,
          data: undefined as T,
          error: error instanceof Error ? error.message : String(error),
        }
      }
      await retryDelay(attempt)
    }
  }
  return { success: false, data: undefined as T, error: '请求失败' }
}

export const apiGet = <T>(path: string) =>
  requestJson<T>(`/api${path}`)

export const apiPost = <T>(path: string, body: unknown) =>
  requestJson<T>(`/api${path}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const apiPut = <T>(path: string, body: unknown) =>
  requestJson<T>(`/api${path}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  })

export const apiPatch = <T>(path: string, body: unknown) =>
  requestJson<T>(`/api${path}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })

export const apiDelete = <T>(path: string) =>
  requestJson<T>(`/api${path}`, { method: 'DELETE' })

export async function fetchBackend(path: string, options: RequestInit = {}) {
  return fetch(`${backendBaseUrl}${path}`, options)
}
