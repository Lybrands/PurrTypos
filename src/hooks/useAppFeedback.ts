import { useToast } from '../ui'

/**
 * 为现有业务提供 message 风格反馈入口，底层统一使用项目内 Toast。
 */
export function useAppFeedback() {
  return { message: useToast() }
}
