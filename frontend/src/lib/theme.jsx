import { useCallback, useEffect, useMemo, useState } from 'react'
import { ThemeContext } from './themeContext'

const KEY = 'claimshield-theme'

function stored() {
  try {
    const value = window.localStorage.getItem(KEY)
    return value === 'light' || value === 'dark' ? value : null
  } catch {
    return null
  }
}

function systemPreference() {
  try {
    return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  } catch {
    return 'dark'
  }
}

/** Holds the light/dark choice: saved choice first, then the system setting, else dark. */
export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => stored() ?? systemPreference())

  useEffect(() => {
    const root = document.documentElement
    root.dataset.theme = theme
    // Enable colour transitions only after the first paint so the page never fades in from the wrong theme.
    const id = requestAnimationFrame(() => root.classList.add('theme-ready'))
    return () => cancelAnimationFrame(id)
  }, [theme])

  // Follow the system setting live until the person picks a theme themselves.
  useEffect(() => {
    const query = window.matchMedia?.('(prefers-color-scheme: light)')
    if (!query?.addEventListener) return undefined
    const onChange = (e) => {
      if (!stored()) setThemeState(e.matches ? 'light' : 'dark')
    }
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const setTheme = useCallback((next) => {
    setThemeState(next)
    try {
      window.localStorage.setItem(KEY, next)
    } catch {
      /* the choice still applies for this visit */
    }
  }, [])

  const value = useMemo(
    () => ({ theme, setTheme, toggle: () => setTheme(theme === 'dark' ? 'light' : 'dark') }),
    [theme, setTheme],
  )
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}
