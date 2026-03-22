import { App as AntdApp } from 'antd'

/**
 * 与 ConfigProvider / 动态主题一致的 message、modal、notification。
 * 请替代静态 `import { message } from 'antd'`，否则会触发 [antd: message] 主题上下文告警。
 */
export function useAntdApp() {
  return AntdApp.useApp()
}
