import React, { useEffect, useState } from 'react'
import { useUiStore } from '../../store/useUiStore'
import { api } from '../../api'
import TrafficLights from './TrafficLights'

type TabId = 'chat' | 'knowledge' | 'tools' | 'usage' | 'settings' | 'audit' | 'skills' | 'connectors' | 'projects' | 'workflows' | 'memory' | 'schedule'

const TABS: Array<{ id: TabId; label: string; icon: string }> = [
  { id: 'chat', label: '对话', icon: '💬' },
  { id: 'knowledge', label: '知识库', icon: '📚' },
  { id: 'tools', label: '工具', icon: '🔧' },
  { id: 'usage', label: '用量', icon: '📊' },
  { id: 'connectors', label: '连接', icon: '🔗' },
  { id: 'projects', label: '项目', icon: '📁' },
  { id: 'workflows', label: '编排', icon: '⚡' },
  { id: 'schedule', label: '定时', icon: '⏰' },
  { id: 'audit', label: '审计', icon: '🛡️' },
  { id: 'skills', label: '技能', icon: '🧩' },
  { id: 'memory', label: '记忆', icon: '🧠' },
  { id: 'settings', label: '设置', icon: '⚙️' },
]

const TopBar: React.FC = () => {
  const { activeTab, setActiveTab } = useUiStore()
  const [unread, setUnread] = useState(0)

  // P2 主动触发：定时结果未读徽标（30s 轮询）
  useEffect(() => {
    let alive = true
    const poll = () => {
      api.schedule
        .notifications()
        .then((n) => {
          if (alive) setUnread(n.unread ?? 0)
        })
        .catch(() => {})
    }
    poll()
    const timer = setInterval(poll, 30000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [])

  return (
    <div
      className="flex items-center gap-1 px-3 py-2 border-b neu-panel neu-topbar"
      style={{
        borderColor: 'var(--border-soft)',
        background: 'var(--bg-panel)',
        backdropFilter: 'var(--glass)',
        WebkitBackdropFilter: 'var(--glass)',
        WebkitAppRegion: 'drag',
      } as React.CSSProperties}
    >
      <TrafficLights />
      {TABS.map((tab) => {
        const active = activeTab === tab.id
        return (
          <button
            key={tab.id}
            className="px-3 py-1.5 rounded-lg text-sm transition-colors relative"
            style={{
              background: active ? 'var(--accent-soft)' : 'transparent',
              color: active ? 'var(--accent)' : 'var(--text-dim)',
              border: 'none',
              cursor: 'pointer',
              fontWeight: active ? 600 : 400,
              WebkitAppRegion: 'no-drag',
            } as React.CSSProperties}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="mr-1">{tab.icon}</span>
            {tab.label}
            {tab.id === 'schedule' && unread > 0 && (
              <span
                className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center"
                style={{ background: 'var(--danger)', color: '#fff' }}
              >
                {unread > 9 ? '9+' : unread}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

export default TopBar
