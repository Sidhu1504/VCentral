import { createContext, useContext, useEffect, useState } from 'react'

const ThemeContext = createContext({ dark: false, toggle: () => {} })

function initialDark() {
  try {
    const saved = localStorage.getItem('vcentral-theme')
    if (saved) return saved === 'dark'
  } catch {
    /* storage unavailable */
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false
}

export function ThemeModeProvider({ children }) {
  const [dark, setDark] = useState(initialDark)
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light'
    document.body.style.background = dark ? '#000' : '#f5f5f5'
  }, [dark])
  const toggle = () =>
    setDark((d) => {
      try {
        localStorage.setItem('vcentral-theme', d ? 'light' : 'dark')
      } catch {
        /* ignore */
      }
      return !d
    })
  return <ThemeContext.Provider value={{ dark, toggle }}>{children}</ThemeContext.Provider>
}

export const useThemeMode = () => useContext(ThemeContext)
