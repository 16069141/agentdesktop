import React from 'react'

const UsageView: React.FC = () => (
  <div className="p-6" style={{ color: 'var(--text)' }}>
    <h2 className="text-lg font-semibold mb-4">用量统计</h2>
    <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>Token 消耗图表（Phase 2）</div>
    <div className="grid grid-cols-2 gap-4">
      <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
        <div className="text-sm font-medium mb-2">近 7 次对话</div>
        <div className="h-32 flex items-end gap-1" style={{ color: 'var(--text-faint)' }}>
          {[40, 65, 45, 80, 55, 70, 60].map((h, i) => (
            <div key={i} className="flex-1 rounded-t" style={{ height: h + '%', background: 'var(--accent-soft)' }}></div>
          ))}
        </div>
      </div>
      <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
        <div className="text-sm font-medium mb-2">今日统计</div>
        <div className="text-2xl font-bold" style={{ color: 'var(--accent)' }}>12,450</div>
        <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>总 tokens</div>
      </div>
    </div>
  </div>
)

export default UsageView
