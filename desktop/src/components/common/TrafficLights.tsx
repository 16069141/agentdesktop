import React, { useState } from 'react'

/**
 * Windows 11 风格窗口控制按钮（右上角）。
 * − 最小化 / □ 最大化还原 / × 关闭。
 * 浅色/深色主题自适应（图标取 --text）；关闭键 hover 红色背景（Windows 惯例）。
 */
const TrafficLights: React.FC = () => {
  const [hovered, setHovered] = useState<'close' | 'min' | 'max' | null>(null)

  const btnBase: React.CSSProperties = {
    width: 44,
    height: '100%',
    minHeight: 28,
    border: 'none',
    background: 'transparent',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
    borderRadius: 4,
    color: 'var(--text-dim)',
    transition: 'background 0.12s ease, color 0.12s ease',
    WebkitAppRegion: 'no-drag',
  } as React.CSSProperties

  const icon = (d: React.ReactNode) => (
    <svg
      width="11"
      height="11"
      viewBox="0 0 10 10"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.1"
      strokeLinecap="round"
    >
      {d}
    </svg>
  )

  const stateStyle = (kind: 'close' | 'min' | 'max'): React.CSSProperties => {
    if (hovered !== kind) return {}
    if (kind === 'close') return { background: '#C42B1C', color: '#fff' }
    return { background: 'var(--bg-hover)', color: 'var(--text)' }
  }

  const clear = () => setHovered(null)

  return (
    <div
      className="flex items-stretch"
      style={{
        marginLeft: 'auto',
        alignSelf: 'stretch',
        WebkitAppRegion: 'no-drag',
      } as React.CSSProperties}
    >
      {/* 最小化 − */}
      <button
        style={{ ...btnBase, ...stateStyle('min') }}
        onClick={() => (window as any).electronAPI?.minimizeWindow()}
        title="最小化"
        onMouseEnter={() => setHovered('min')}
        onMouseLeave={clear}
      >
        {icon(<path d="M2 5h6" />)}
      </button>
      {/* 最大化/还原 □ */}
      <button
        style={{ ...btnBase, ...stateStyle('max') }}
        onClick={() => (window as any).electronAPI?.maximizeWindow()}
        title="最大化/还原"
        onMouseEnter={() => setHovered('max')}
        onMouseLeave={clear}
      >
        {icon(<rect x="1.6" y="1.6" width="6.8" height="6.8" />)}
      </button>
      {/* 关闭 × */}
      <button
        style={{ ...btnBase, ...stateStyle('close') }}
        onClick={() => (window as any).electronAPI?.closeWindow()}
        title="关闭"
        onMouseEnter={() => setHovered('close')}
        onMouseLeave={clear}
      >
        {icon(<path d="M2.2 2.2l5.6 5.6M7.8 2.2l-5.6 5.6" />)}
      </button>
    </div>
  )
}

export default TrafficLights
