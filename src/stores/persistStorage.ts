import { createJSONStorage } from 'zustand/middleware'

/**
 * 共享 JSON 持久化 storage：写失败不吞（记录后不影响内存态运行）；
 * 无 localStorage 的环境（SSR / node 单测）整体降级为空实现。
 * 各 persist store 统一使用，替代手写 localStorage 的散点容错。
 */

const getLocalStorage = (): Storage | null => {
  try {
    return typeof globalThis.localStorage === 'undefined'
      ? null
      : globalThis.localStorage
  } catch {
    return null
  }
}

export const loggedJsonStorage = createJSONStorage(() => {
  const ls = getLocalStorage()
  if (ls == null) {
    return {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
    }
  }
  return {
    getItem: (name: string) => {
      try {
        return ls.getItem(name)
      } catch (error) {
        console.error(`[persist:${name}] read failed:`, error)
        return null
      }
    },
    setItem: (name: string, value: string) => {
      try {
        ls.setItem(name, value)
      } catch (error) {
        console.error(`[persist:${name}] write failed:`, error)
      }
    },
    removeItem: (name: string) => {
      try {
        ls.removeItem(name)
      } catch (error) {
        console.error(`[persist:${name}] remove failed:`, error)
      }
    },
  }
})
