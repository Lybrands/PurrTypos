import React from 'react'
import ReactDOM from 'react-dom/client'
import { App as AntdApp, ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { ThemeProvider, useTheme } from './contexts/ThemeContext'
import { FontSizeProvider } from './contexts/FontSizeContext'
import App from './App'
import './index.scss'

function ThemedApp() {
  const { theme: themeMode } = useTheme()
  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: themeMode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
      }}
    >
      <AntdApp>
        <App />
      </AntdApp>
    </ConfigProvider>
  )
}

const rootEl = document.getElementById('root')
try {
  ReactDOM.createRoot(rootEl!).render(
    <React.StrictMode>
      <ThemeProvider>
        <FontSizeProvider>
          <ThemedApp />
        </FontSizeProvider>
      </ThemeProvider>
    </React.StrictMode>
  )
} catch (e) {
  throw e
}
