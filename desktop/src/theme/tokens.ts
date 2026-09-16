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
    '--bg': '#E8EDF4',
    '--bg-panel': 'rgba(255,255,255,0.88)',
    '--bg-elev': 'rgba(255,255,255,0.96)',
    '--bg-hover': 'rgba(25,45,75,0.10)',
    '--border': 'rgba(20,40,70,0.24)',
    '--border-soft': 'rgba(20,40,70,0.15)',
    '--text': '#101826',
    '--text-dim': '#36445A',
    '--text-faint': '#5F6E85',
    '--glass': 'blur(24px) saturate(170%)',
    '--mono': '"SF Mono", "JetBrains Mono", Menlo, Consolas, monospace',
    '--sans': '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
    // 语义色（浅色）
    '--surf-input': '#FFFFFF',
    '--thinking': '#4A5AD0',
    '--tool': '#9E6A12',
    '--ok': '#178A64',
    '--warn': '#9E6A12',
    '--danger': '#C22B4F',
    '--code-bg': 'rgba(20,40,70,0.09)',
  },
}

export const accentColors = {
  teal: {
    // 深色主题：亮青（深底上醒目）
    dark: {
      '--accent': '#3FD8BE',
      '--accent-ink': '#0B211C',
      '--accent-soft': 'rgba(63,216,190,0.16)',
      '--glow-1': 'rgba(63,216,190,0.17)',
      '--glow-2': 'rgba(110,156,240,0.20)',
    },
    // 浅色主题：深青（白底对比足、不刺眼）
    light: {
      '--accent': '#0E9F84',
      '--accent-ink': '#FFFFFF',
      '--accent-soft': 'rgba(14,159,132,0.14)',
      '--glow-1': 'rgba(14,159,132,0.12)',
      '--glow-2': 'rgba(74,118,220,0.10)',
    },
  },
  violet: {
    dark: {
      '--accent': '#A78BFA',
      '--accent-ink': '#221A3A',
      '--accent-soft': 'rgba(167,139,250,0.18)',
      '--glow-1': 'rgba(167,139,250,0.20)',
      '--glow-2': 'rgba(124,156,245,0.19)',
    },
    light: {
      '--accent': '#6C4FD8',
      '--accent-ink': '#FFFFFF',
      '--accent-soft': 'rgba(108,79,216,0.14)',
      '--glow-1': 'rgba(108,79,216,0.12)',
      '--glow-2': 'rgba(124,156,245,0.10)',
    },
  },
  amber: {
    dark: {
      '--accent': '#F2B35E',
      '--accent-ink': '#2E2008',
      '--accent-soft': 'rgba(242,179,94,0.18)',
      '--glow-1': 'rgba(242,179,94,0.19)',
      '--glow-2': 'rgba(240,130,120,0.15)',
    },
    light: {
      '--accent': '#C07D16',
      '--accent-ink': '#FFFFFF',
      '--accent-soft': 'rgba(192,125,22,0.16)',
      '--glow-1': 'rgba(192,125,22,0.13)',
      '--glow-2': 'rgba(240,130,120,0.10)',
    },
  },
  rose: {
    dark: {
      '--accent': '#F07C9C',
      '--accent-ink': '#2E1320',
      '--accent-soft': 'rgba(240,124,156,0.18)',
      '--glow-1': 'rgba(240,124,156,0.20)',
      '--glow-2': 'rgba(167,139,250,0.16)',
    },
    light: {
      '--accent': '#D14E76',
      '--accent-ink': '#FFFFFF',
      '--accent-soft': 'rgba(209,78,118,0.14)',
      '--glow-1': 'rgba(209,78,118,0.12)',
      '--glow-2': 'rgba(167,139,250,0.10)',
    },
  },
}
