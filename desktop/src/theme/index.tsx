import { useEffect } from 'react'
import { useUiStore } from '../store/useUiStore'
import { themeTokens, accentColors } from './tokens'

export function useTheme() {
  const { theme, accent } = useUiStore()

  useEffect(() => {
    const root = document.documentElement
    const t = themeTokens[theme]
    // 强调色分深/浅两套：浅色主题用深色强调色（白底对比足、不刺眼）
    const a = accentColors[accent][theme]

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
