import React, { useEffect, useState } from 'react'

interface RecentUsage {
  conversationTitle?: string
  modelId?: string
  promptTokens: number
  completionTokens: number
  toolCalls: number
  createdAt: number
}

interface DailyUsage {
  day: string
  promptTokens: number
  completionTokens: number
  toolCalls: number
}

const apiBase = 'http://127.0.0.1:8766'

const UsageView: React.FC = () => {
  const [recent, setRecent] = useState<RecentUsage[]>([])
  const [daily, setDaily] = useState<DailyUsage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const [exportBy, setExportBy] = useState<'day' | 'connector' | 'user' | 'model' | 'tool'>('day')

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const token = await window.electronAPI?.getAgentToken() || ''
      const headers: Record<string, string> = {}
      if (token) headers.Authorization = `Bearer ${token}`

      const [recentRes, dailyRes] = await Promise.all([
        fetch(`${apiBase}/api/usage/recent?n=7`, { headers }),
        fetch(`${apiBase}/api/usage/daily?days=7`, { headers }),
      ])
      if (!recentRes.ok || !dailyRes.ok) {
        throw new Error(`HTTP ${recentRes.status}/${dailyRes.status}`)
      }
      setRecent((await recentRes.json()) as RecentUsage[])
      setDaily((await dailyRes.json()) as DailyUsage[])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  /** 成本分摊导出（P2：按天 / 连接器 / 用户，CSV 下载） */
  const exportCsv = async () => {
    setExporting(true)
    setError(null)
    try {
      const token = await window.electronAPI?.getAgentToken() || ''
      const headers: Record<string, string> = {}
      if (token) headers.Authorization = `Bearer ${token}`
      const resp = await fetch(`${apiBase}/api/usage/export?by=${exportBy}&days=30&fmt=csv`, { headers })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const data = (await resp.json()) as { csv: string }
      const blob = new Blob(['\ufeff' + data.csv], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `usage-${exportBy}-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setExporting(false)
    }
  }

  const totalTokens = recent.reduce((s, r) => s + r.promptTokens + r.completionTokens, 0)
  const maxDaily = daily.length ? Math.max(...daily.map((d) => d.promptTokens + d.completionTokens), 1) : 1

  const formatTime = (ts?: number) => {
    if (!ts) return '-'
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  }

  return (
    <div className="p-6" style={{ color: 'var(--text)' }}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold mb-1">用量统计</h2>
          <div className="text-sm" style={{ color: 'var(--text-dim)' }}>Token 消耗统计（近 7 次对话 · 近 7 天聚合）</div>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={exportBy}
            onChange={(e) => setExportBy(e.target.value as any)}
            className="text-sm rounded-lg px-2 py-1.5 outline-none"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          >
            <option value="day">按天</option>
            <option value="connector">按连接器</option>
            <option value="user">按用户</option>
            <option value="model">按模型</option>
            <option value="tool">按工具</option>
          </select>
          <button
            onClick={exportCsv}
            disabled={exporting}
            className="px-3 py-1.5 rounded-lg text-sm"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
          >
            {exporting ? '导出中...' : '导出 CSV（成本分摊）'}
          </button>
          <button
            onClick={load}
            className="px-3 py-1.5 rounded-lg text-sm"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
          >
            刷新
          </button>
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          加载失败：{error}
        </div>
      )}

      {loading ? (
        <div className="text-center py-8" style={{ color: 'var(--text-faint)' }}>加载中...</div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 mb-6">
            <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div className="text-sm font-medium mb-2">近 7 次对话</div>
              <div className="flex h-32 items-end gap-1" style={{ color: 'var(--text-faint)' }}>
                {recent.length === 0 && <div className="text-xs py-4">暂无数据，先进行几次对话</div>}
                {recent.map((r, i) => {
                  const h = r.promptTokens + r.completionTokens
                  const height = h > 0 ? Math.max(8, Math.round((h / maxDaily) * 100)) : 4
                  return (
                    <div key={i} className="flex-1 flex items-end" title={`${formatTime(r.createdAt)}: ${h} tokens`}>
                      <div className="w-full rounded-t" style={{ height: height + '%', background: 'var(--accent-soft)' }} />
                    </div>
                  )
                })}
              </div>
            </div>
            <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div className="text-sm font-medium mb-2">近 7 次合计</div>
              <div className="text-2xl font-bold" style={{ color: 'var(--accent)' }}>
                {totalTokens.toLocaleString()}
              </div>
              <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
                total tokens · {recent.filter((r) => r.toolCalls > 0).length} 次含工具调用
              </div>
            </div>
          </div>

          <div className="mb-6">
            <h3 className="font-medium mb-3">近 7 天每日消耗</h3>
            {daily.length === 0 ? (
              <div className="text-sm p-4 rounded-lg text-center" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)' }}>
                暂无数据
              </div>
            ) : (
              <div className="flex h-28 items-end gap-2" style={{ color: 'var(--text-faint)' }}>
                {daily.map((d) => {
                  const h = d.promptTokens + d.completionTokens
                  const height = h > 0 ? Math.max(8, Math.round((h / maxDaily) * 100)) : 4
                  return (
                    <div key={d.day} className="flex-1 flex flex-col items-center gap-1">
                      <div className="w-full rounded-t" style={{ height: height + '%', background: 'var(--accent-soft)' }} />
                      <span className="text-[10px]">{d.day}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div>
            <h3 className="font-medium mb-3">最近对话明细</h3>
            {recent.length === 0 ? (
              <div className="text-sm p-4 rounded-lg text-center" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)' }}>
                暂无记录
              </div>
            ) : (
              <div className="overflow-x-auto rounded-lg" style={{ border: '1px solid var(--border-soft)' }}>
                <table className="w-full text-sm" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}>
                      <th className="px-3 py-2 text-left font-medium">会话</th>
                      <th className="px-3 py-2 text-left font-medium">模型</th>
                      <th className="px-3 py-2 text-right font-medium">Prompt</th>
                      <th className="px-3 py-2 text-right font-medium">Completion</th>
                      <th className="px-3 py-2 text-right font-medium">工具调用</th>
                      <th className="px-3 py-2 text-left font-medium">时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((r, i) => (
                      <tr key={i} style={{ borderTop: '1px solid var(--border-soft)' }}>
                        <td className="px-3 py-2 max-w-[160px] truncate">{r.conversationTitle || '（未命名）'}</td>
                        <td className="px-3 py-2">{r.modelId || '-'}</td>
                        <td className="px-3 py-2 text-right">{r.promptTokens.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right">{r.completionTokens.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right">{r.toolCalls}</td>
                        <td className="px-3 py-2">{formatTime(r.createdAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

export default UsageView
