import React from 'react'
import ReactDOM from 'react-dom/client'
import {
  BrowserRouter,
  HashRouter,
} from 'react-router-dom'
import { ThemeProvider } from './contexts/ThemeContext'
import App from './App'
import { runtimeCapabilities } from './platform'
import { initializeWindowChrome } from './platform/windowChrome'
import { PurrConfirmProvider, PurrToastProvider, PurrTooltipProvider } from '@/purr-components'
import './index.scss'

const disposeWindowChrome = initializeWindowChrome()
import.meta.hot?.dispose(disposeWindowChrome)

function AppProviders() {
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

function ThemedApp() {
  const Router = runtimeCapabilities.runtime === 'electron' ? HashRouter : BrowserRouter
  return (
    <Router>
      <AppProviders />
    </Router>
  )
}

const rootEl = document.getElementById('root')
try {
  ReactDOM.createRoot(rootEl!).render(
    <React.StrictMode>
      <ThemeProvider>
        <ThemedApp />
      </ThemeProvider>
    </React.StrictMode>
  )
} catch (e) {
  throw e
}
