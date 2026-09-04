import React from 'react'
import { useUiStore } from '../../store/useUiStore'

type TabId = 'chat' | 'knowledge' | 'tools' | 'usage' | 'settings'

const TABS: Array<{ id: TabId; label: string; icon: string }> = [
  { id: 'chat', label: '对话', icon: '💬' },
  { id: 'knowledge', label: '知识库', icon: '📚' },
  { id: 'tools', label: '工具', icon: '🔧' },
  { id: 'usage', label: '用量', icon: '📊' },
  { id: 'settings', label: '设置', icon: '⚙️' },
]

const TopBar: React.FC = () => {
  const { activeTab, setActiveTab } = useUiStore()

  return (
    <div
      className="flex items-center gap-1 px-3 py-2 border-b"
      style={{
        borderColor: 'var(--border-soft)',
        background: 'var(--bg-panel)',
        backdropFilter: 'var(--glass)',
        WebkitBackdropFilter: 'var(--glass)',
      }}
    >
      {TABS.map((tab) => {
        const active = activeTab === tab.id
        return (
          <button
            key={tab.id}
            className="px-3 py-1.5 rounded-lg text-sm transition-colors"
            style={{
              background: active ? 'var(--accent-soft)' : 'transparent',
              color: active ? 'var(--accent)' : 'var(--text-dim)',
              border: 'none',
              cursor: 'pointer',
              fontWeight: active ? 600 : 400,
            }}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="mr-1">{tab.icon}</span>
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

export default TopBar
