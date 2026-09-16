export const themeTokens = {
  dark: {
    '--bg': '#0A0F1A',
    '--bg-panel': 'rgba(255,255,255,0.055)',
    '--bg-elev': 'rgba(255,255,255,0.09)',
    '--bg-hover': 'rgba(255,255,255,0.12)',
    '--border': 'rgba(255,255,255,0.14)',
    '--border-soft': 'rgba(255,255,255,0.09)',
    '--text': '#EEF3F9',
    '--text-dim': '#A8B5C6',
    '--text-faint': '#6E7B8F',
    '--glass': 'blur(24px) saturate(170%)',
    '--mono': '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace',
    '--sans': '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    // 语义色（暗色）
    '--surf-input': 'rgba(255,255,255,0.06)',
    '--thinking': '#8B9DF5',
    '--tool': '#E0A45C',
    '--ok': '#3FD8A0',
    '--warn': '#F2B35E',
    '--danger': '#F2647C',
    '--code-bg': 'rgba(0,0,0,0.32)',
  },
  light: {
    '--bg': '#EEF2F8',
    '--bg-panel': 'rgba(255,255,255,0.55)',
    '--bg-elev': 'rgba(255,255,255,0.72)',
    '--bg-hover': 'rgba(25,45,75,0.07)',
    '--border': 'rgba(20,40,70,0.15)',
    '--border-soft': 'rgba(20,40,70,0.09)',
    '--text': '#1B2434',
    '--text-dim': '#5A6678',
    '--text-faint': '#8B95A5',
    '--glass': 'blur(24px) saturate(170%)',
    '--mono': '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace',
    '--sans': '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    // 语义色（浅色）
    '--surf-input': 'rgba(255,255,255,0.78)',
    '--thinking': '#5B6BD6',
    '--tool': '#B87333',
    '--ok': '#1E9E75',
    '--warn': '#B57C1E',
    '--danger': '#D0385B',
    '--code-bg': 'rgba(20,40,70,0.06)',
  },
}

export const accentColors = {
  teal: {
    '--accent': '#3FD8BE',
    '--accent-ink': '#0B211C',
    '--accent-soft': 'rgba(63,216,190,0.16)',
    '--glow-1': 'rgba(63,216,190,0.17)',
    '--glow-2': 'rgba(110,156,240,0.20)',
  },
  violet: {
    '--accent': '#A78BFA',
    '--accent-ink': '#221A3A',
    '--accent-soft': 'rgba(167,139,250,0.18)',
    '--glow-1': 'rgba(167,139,250,0.20)',
    '--glow-2': 'rgba(124,156,245,0.19)',
  },
  amber: {
    '--accent': '#F2B35E',
    '--accent-ink': '#2E2008',
    '--accent-soft': 'rgba(242,179,94,0.18)',
    '--glow-1': 'rgba(242,179,94,0.19)',
    '--glow-2': 'rgba(240,130,120,0.15)',
  },
  rose: {
    '--accent': '#F07C9C',
    '--accent-ink': '#2E1320',
    '--accent-soft': 'rgba(240,124,156,0.18)',
    '--glow-1': 'rgba(240,124,156,0.20)',
    '--glow-2': 'rgba(167,139,250,0.16)',
  },
}

// Windows 11 Fluent 风格：覆盖基础色板 + 提供浮动阴影变量。
// 与深色/浅色主题、强调色正交组合；扁平风格不注入这些变量。
export const windowsTokens = {
  dark: {
    '--bg': '#1C1C1C',
    '--bg-panel': 'rgba(255,255,255,0.045)',
    '--bg-elev': 'rgba(255,255,255,0.075)',
    '--bg-hover': 'rgba(255,255,255,0.09)',
    '--border': 'rgba(255,255,255,0.12)',
    '--border-soft': 'rgba(255,255,255,0.07)',
    '--surf-input': 'rgba(0,0,0,0.28)',
    '--code-bg': 'rgba(0,0,0,0.35)',
    // 浮动阴影：卡片 / 浮层（Windows 11 柔和投影，无双向高光）
    '--win-shadow-card': '0 2px 10px rgba(0,0,0,0.35)',
    '--win-shadow-float': '0 8px 24px rgba(0,0,0,0.4)',
    // 贴边面板：侧栏 / 顶栏（细分隔线，不投影）
    '--win-edge-line': '1px solid rgba(255,255,255,0.09)',
    '--win-strip-line': '1px solid rgba(255,255,255,0.08)',
  },
  light: {
    '--bg': '#F2F2F2',
    '--bg-panel': 'rgba(255,255,255,0.72)',
    '--bg-elev': 'rgba(255,255,255,0.9)',
    '--bg-hover': 'rgba(20,40,70,0.06)',
    '--border': 'rgba(20,40,70,0.13)',
    '--border-soft': 'rgba(20,40,70,0.08)',
    '--surf-input': 'rgba(255,255,255,0.9)',
    '--code-bg': 'rgba(20,40,70,0.06)',
    // 浮动阴影：卡片 / 浮层（Windows 11 柔和投影，无双向高光）
    '--win-shadow-card': '0 2px 10px rgba(20,40,70,0.10)',
    '--win-shadow-float': '0 8px 24px rgba(20,40,70,0.14)',
    // 贴边面板：侧栏 / 顶栏（细分隔线，不投影）
    '--win-edge-line': '1px solid rgba(20,40,70,0.09)',
    '--win-strip-line': '1px solid rgba(20,40,70,0.08)',
  },
}
