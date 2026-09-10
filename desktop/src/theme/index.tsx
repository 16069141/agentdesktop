import { useEffect } from 'react'
import { useUiStore } from '../store/useUiStore'
import { themeTokens, accentColors } from './tokens'

export function useTheme() {
  const { theme, accent } = useUiStore()

  useEffect(() => {
    const root = document.documentElement
    const t = themeTokens[theme]
    const a = accentColors[accent]

    for (const [key, value] of Object.entries(t)) {
      root.style.setProperty(key, value)
    }
    for (const [key, value] of Object.entries(a)) {
      root.style.setProperty(key, value)
    }

    root.setAttribute('data-theme', theme)
    root.setAttribute('data-accent', accent)
  }, [theme, accent])
}
