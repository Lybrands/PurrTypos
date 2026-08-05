import React from 'react'
import ReactDOM from 'react-dom/client'
import { ThemeProvider } from './contexts/ThemeContext'
import { FontSizeProvider } from './contexts/FontSizeContext'
import App from './App'
import { PurrConfirmProvider, PurrToastProvider, PurrTooltipProvider } from '@/purr-components'
import './index.scss'

function ThemedApp() {
  return (
    <PurrTooltipProvider>
      <PurrToastProvider>
        <PurrConfirmProvider>
          <App />
        </PurrConfirmProvider>
      </PurrToastProvider>
    </PurrTooltipProvider>
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
