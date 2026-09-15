import { useEffect } from 'react'
import { useUiStore } from '../store/useUiStore'
import { themeTokens, accentColors, neuTokens } from './tokens'

export function useTheme() {
  const { theme, accent, uiStyle } = useUiStore()

  useEffect(() => {
    const root = document.documentElement
    // 新拟物风格在基础色板上叠加 neu 色板（覆盖表面色 + 提供软阴影变量）
    const t = {
      ...themeTokens[theme],
      ...(uiStyle === 'neu' ? neuTokens[theme] : {}),
    }
    const a = accentColors[accent]

    for (const [key, value] of Object.entries(t)) {
      root.style.setProperty(key, value)
    }
    for (const [key, value] of Object.entries(a)) {
      root.style.setProperty(key, value)
    }

    root.setAttribute('data-theme', theme)
    root.setAttribute('data-accent', accent)
    root.setAttribute('data-style', uiStyle)
  }, [theme, accent, uiStyle])
}
