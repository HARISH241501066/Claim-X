import { createContext, useContext } from 'react'
import { NODE_PALETTES, PALETTES } from '../theme'

// Outside a provider (unit tests, isolated renders) the app behaves as dark and ignores toggles.
export const ThemeContext = createContext({ theme: 'dark', setTheme: () => {}, toggle: () => {} })

export function useTheme() {
  return useContext(ThemeContext)
}

/** Palettes for canvas and SVG drawing that follow the active theme. */
export function useThemeColors() {
  const { theme } = useTheme()
  return { colors: PALETTES[theme] ?? PALETTES.dark, nodeColors: NODE_PALETTES[theme] ?? NODE_PALETTES.dark }
}
