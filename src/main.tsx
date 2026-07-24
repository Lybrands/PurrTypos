import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider } from './contexts/ThemeContext'
import { FontSizeProvider } from './contexts/FontSizeContext'
import App from './App'
import { ConfirmProvider, ToastProvider, TooltipProvider } from './ui'
import './index.scss'

function ThemedApp() {
  return (
    <TooltipProvider>
      <ToastProvider>
        <ConfirmProvider>
          <App />
        </ConfirmProvider>
      </ToastProvider>
    </TooltipProvider>
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
