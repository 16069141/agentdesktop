import React, { useState } from 'react'

/**
 * macOS 风格红绿灯窗口控制按钮。
 * 红=关闭，黄=最小化，绿=最大化/还原。
 * hover 时显示对应图标，参照 macOS 原生样式。
 */
const TrafficLights: React.FC = () => {
  const [hovered, setHovered] = useState(false)

  const btnBase: React.CSSProperties = {
    width: '12px',
    height: '12px',
    borderRadius: '50%',
    border: 'none',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 0,
    transition: 'filter 0.15s',
    WebkitAppRegion: 'no-drag',
  } as React.CSSProperties

  const iconStyle: React.CSSProperties = {
    fontSize: '8px',
    lineHeight: 1,
    color: 'rgba(0,0,0,0.5)',
    fontWeight: 700,
    userSelect: 'none',
  }

  return (
    <div
      className="flex items-center gap-2 mr-3"
      style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* 关闭 */}
      <button
        style={{ ...btnBase, background: '#FF5F57' }}
        onClick={() => (window as any).electronAPI?.closeWindow()}
        title="关闭"
      >
        {hovered && <span style={iconStyle}>×</span>}
      </button>
      {/* 最小化 */}
      <button
        style={{ ...btnBase, background: '#FEBC2E' }}
        onClick={() => (window as any).electronAPI?.minimizeWindow()}
        title="最小化"
      >
        {hovered && <span style={{ ...iconStyle, fontSize: '10px', marginTop: '-2px' }}>−</span>}
      </button>
      {/* 最大化/还原 */}
      <button
        style={{ ...btnBase, background: '#28C840' }}
        onClick={() => (window as any).electronAPI?.maximizeWindow()}
        title="最大化/还原"
      >
        {hovered && (
          <svg width="7" height="7" viewBox="0 0 10 10" fill="none" stroke="rgba(0,0,0,0.5)" strokeWidth="1.2">
            <path d="M2 2h6v6H2z M2 8L8 2" />
          </svg>
        )}
      </button>
    </div>
  )
}

export default TrafficLights
