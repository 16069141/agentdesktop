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

// 新拟物（Neumorphism）风格：覆盖基础色板 + 提供软阴影变量。
// 与深色/浅色主题、强调色正交组合；扁平风格不注入这些变量。
export const neuTokens = {
  dark: {
    '--bg': '#232730',
    '--bg-panel': '#262B35',
    '--bg-elev': '#2A303B',
    '--bg-hover': 'rgba(255,255,255,0.05)',
    '--border': 'rgba(255,255,255,0.06)',
    '--border-soft': 'rgba(255,255,255,0.035)',
    '--surf-input': '#232730',
    '--code-bg': 'rgba(0,0,0,0.35)',
    // 软阴影：外凸 / 内凹（暗色：深色在下、高光在上）
    '--neu-shadow-out': '8px 8px 16px rgba(0,0,0,0.5), -8px -8px 16px rgba(255,255,255,0.07)',
    '--neu-shadow-out-sm': '4px 4px 10px rgba(0,0,0,0.45), -4px -4px 10px rgba(255,255,255,0.06)',
    '--neu-shadow-in': 'inset 5px 5px 10px rgba(0,0,0,0.55), inset -5px -5px 10px rgba(255,255,255,0.06)',
    '--neu-shadow-in-sm': 'inset 3px 3px 7px rgba(0,0,0,0.5), inset -3px -3px 7px rgba(255,255,255,0.05)',
    // 贴边面板：侧栏 / 顶栏（只向外侧投影）
    '--neu-edge-shadow': '8px 0 16px -8px rgba(0,0,0,0.6), -2px 0 8px -4px rgba(255,255,255,0.08)',
    '--neu-strip-shadow': '0 8px 16px -10px rgba(0,0,0,0.55), 0 -2px 8px -4px rgba(255,255,255,0.07)',
  },
  light: {
    '--bg': '#E3E8F1',
    '--bg-panel': '#E6EBF4',
    '--bg-elev': '#EBF0F7',
    '--bg-hover': 'rgba(25,45,75,0.06)',
    '--border': 'rgba(20,40,70,0.06)',
    '--border-soft': 'rgba(20,40,70,0.035)',
    '--surf-input': '#E3E8F1',
    '--code-bg': 'rgba(255,255,255,0.55)',
    // 软阴影：外凸 / 内凹（浅色：高光在上、深色在下）
    '--neu-shadow-out': '8px 8px 16px rgba(163,174,191,0.55), -8px -8px 16px rgba(255,255,255,0.9)',
    '--neu-shadow-out-sm': '4px 4px 10px rgba(163,174,191,0.5), -4px -4px 10px rgba(255,255,255,0.85)',
    '--neu-shadow-in': 'inset 5px 5px 10px rgba(163,174,191,0.55), inset -5px -5px 10px rgba(255,255,255,0.9)',
    '--neu-shadow-in-sm': 'inset 3px 3px 7px rgba(163,174,191,0.5), inset -3px -3px 7px rgba(255,255,255,0.85)',
    '--neu-edge-shadow': '8px 0 16px -8px rgba(40,60,90,0.28), -2px 0 8px -4px rgba(255,255,255,0.75)',
    '--neu-strip-shadow': '0 8px 16px -10px rgba(40,60,90,0.3), 0 -2px 8px -4px rgba(255,255,255,0.7)',
  },
}
