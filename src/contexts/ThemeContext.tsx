import React from 'react'

const STORAGE_KEY = 'purrtypos_theme'

export type ThemeMode = 'light' | 'dark'

interface ThemeContextValue {
  theme: ThemeMode
  setTheme: (mode: ThemeMode) => void
  toggleTheme: () => void
}

const ThemeContext = React.createContext<ThemeContextValue | null>(null)

function getStoredTheme(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'dark' || stored === 'light') return stored
  } catch {}
  return 'light'
}

function applyTheme(mode: ThemeMode) {
  document.documentElement.setAttribute('data-theme', mode)
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = React.useState<ThemeMode>(() => {
    const stored = getStoredTheme()
    applyTheme(stored)
    return stored
  })

  React.useEffect(() => {
    applyTheme(theme)
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const setTheme = React.useCallback((mode: ThemeMode) => {
    setThemeState(mode)
  }, [])

  const toggleTheme = React.useCallback(() => {
    setThemeState((prev) => (prev === 'light' ? 'dark' : 'light'))
  }, [])

  const value = React.useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme]
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const ctx = React.useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
