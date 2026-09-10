import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'
import ReplayVisual from './ReplayVisual'

interface AuditEntry {
  id: number
  conversationId: string
  actor: string
  toolName: string
  action: string
  paramsRedacted: string
  resultPreview: string
  riskLevel: string
  approved: number | null
  latencyMs: number
  createdAt: number
}

interface BreakerState {
  toolName: string
  callsInWindow: number
  threshold: number
  windowSec: number
  open: boolean
}

const RISK_COLOR: Record<string, string> = {
  low: 'var(--ok)',
  medium: 'var(--warn)',
  high: 'var(--danger)',
}

const fmtTime = (ms: number) =>
  new Date(ms).toLocaleString('zh-CN', { hour12: false })

const AuditView: React.FC = () => {
  const [logs, setLogs] = useState<AuditEntry[]>([])
  const [breakers, setBreakers] = useState<BreakerState[]>([])
  const [filters, setFilters] = useState<{ conversation_id?: string; tool_name?: string; actor?: string }>({})
  const [replayConv, setReplayConv] = useState('')
  const [replay, setReplay] = useState<AuditEntry[] | null>(null)
  const [replayStats, setReplayStats] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (f: typeof filters = filters) => {
    setLoading(true)
    setError(null)
    try {
      const [logsData, breakersData] = await Promise.all([
        api.audit.logs(f),
        api.audit.breakers(),
      ])
      setLogs(Array.isArray(logsData) ? logsData : [])
      setBreakers(Array.isArray(breakersData) ? breakersData : [])
    } catch (e) {
      setError(`加载审计数据失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [])

  const handleReplay = async () => {
    if (!replayConv.trim()) return
    setError(null)
    try {
      const [data, stats] = await Promise.all([
        api.audit.replay(replayConv.trim()),
        api.audit.replayStats(replayConv.trim()),
      ])
      setReplay(Array.isArray(data) ? data : [])
      setReplayStats(stats)
    } catch (e) {
      setError(`回放失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const resetBreaker = async (toolName?: string) => {
    await api.audit.resetBreaker(toolName)
    load()
  }

  const renderEntry = (e: AuditEntry) => (
    <div
      key={e.id}
      className="rounded-xl p-3 mb-2 text-xs"
      style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}
    >
      <div className="flex items-center gap-2 flex-wrap mb-1">
        <span className="font-mono" style={{ color: 'var(--accent)' }}>{e.toolName}</span>
        <span
          className="px-1.5 py-0.5 rounded font-mono"
          style={{
            background: `${RISK_COLOR[e.riskLevel] || 'var(--warn)'}22`,
            color: RISK_COLOR[e.riskLevel] || 'var(--warn)',
          }}
        >
          {e.riskLevel}
        </span>
        <span style={{ color: 'var(--text-faint)' }}>{e.action}</span>
        {e.approved !== null && (
          <span style={{ color: e.approved ? 'var(--ok)' : 'var(--danger)' }}>
            {e.approved ? '已批准' : '已拒绝'}
          </span>
        )}
        <span className="ml-auto" style={{ color: 'var(--text-faint)' }}>{fmtTime(e.createdAt)}</span>
      </div>
      <div className="mb-1">
        <span style={{ color: 'var(--text-faint)' }}>调用者 </span>
        <span style={{ color: 'var(--text-dim)' }}>{e.actor}</span>
        <span className="mx-2" style={{ color: 'var(--text-faint)' }}>|</span>
        <span style={{ color: 'var(--text-faint)' }}>耗时 </span>
        <span style={{ color: 'var(--text-dim)' }}>{e.latencyMs}ms</span>
        <span className="mx-2" style={{ color: 'var(--text-faint)' }}>|</span>
        <span style={{ color: 'var(--text-faint)' }}>会话 </span>
        <span className="font-mono" style={{ color: 'var(--text-dim)' }}>{e.conversationId.slice(0, 8)}</span>
      </div>
      <div className="font-mono rounded p-2 mb-1" style={{ background: 'var(--code-bg)' }}>
        <div style={{ color: 'var(--text-dim)' }}>Params(脱敏): {e.paramsRedacted}</div>
        <div style={{ color: 'var(--text-faint)' }}>Result(截断): {e.resultPreview.slice(0, 200)}</div>
      </div>
    </div>
  )

  return (
    <div className="p-6 overflow-y-auto h-full" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">安全审计与合规</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        工具调用完整链路（Who/When/What/Params/Result，追加只写）· 敏感字段脱敏 · 高频调用熔断
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}

      {/* 筛选 */}
      <div className="flex gap-2 mb-4 flex-wrap">
        <input
          className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
          style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
          placeholder="会话 ID 筛选"
          value={filters.conversation_id || ''}
          onChange={(e) => setFilters({ ...filters, conversation_id: e.target.value })}
        />
        <input
          className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
          style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
          placeholder="工具名筛选"
          value={filters.tool_name || ''}
          onChange={(e) => setFilters({ ...filters, tool_name: e.target.value })}
        />
        <input
          className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
          style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
          placeholder="调用者筛选"
          value={filters.actor || ''}
          onChange={(e) => setFilters({ ...filters, actor: e.target.value })}
        />
        <button
          className="px-3 py-1.5 rounded-lg text-sm"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
          onClick={() => load()}
        >
          查询
        </button>
        <button
          className="px-3 py-1.5 rounded-lg text-sm"
          style={{ color: 'var(--text-dim)' }}
          onClick={() => { setFilters({}); load({}) }}
        >
          清空
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* 审计日志 */}
        <div className="lg:col-span-2">
          <div className="font-medium mb-2 text-sm">审计日志（{logs.length}）</div>
          {loading ? <div style={{ color: 'var(--text-faint)' }}>加载中...</div> :
            logs.length === 0 ? <div className="text-sm py-6 text-center" style={{ color: 'var(--text-faint)' }}>暂无审计记录</div> :
            logs.slice(0, 50).map(renderEntry)}
        </div>

        {/* 右侧：操作回放 + 熔断 */}
        <div className="space-y-4">
          <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
            <div className="font-medium mb-2 text-sm">操作回放</div>
            <div className="flex gap-2">
              <input
                className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                placeholder="输入会话 ID"
                value={replayConv}
                onChange={(e) => setReplayConv(e.target.value)}
              />
              <button
                className="px-3 py-1.5 rounded-lg text-sm"
                style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                onClick={handleReplay}
              >
                回放
              </button>
            </div>
            {replay && (
              <div className="mt-2 max-h-64 overflow-y-auto space-y-1">
                {replay.length === 0 && <div style={{ color: 'var(--text-faint)' }}>无调用链</div>}
                {replay.map((e, i) => (
                  <div key={i} className="text-xs p-1.5 rounded" style={{ background: 'var(--code-bg)' }}>
                    <span className="text-faint mr-1" style={{ color: 'var(--text-faint)' }}>#{i + 1}</span>
                    <span style={{ color: 'var(--accent)' }}>{e.toolName}</span>
                    <span style={{ color: 'var(--text-dim)' }}> {e.action}</span>
                    <span className="ml-1 font-mono" style={{ color: 'var(--text-faint)' }}>
                      {e.paramsRedacted.slice(0, 80)}
                    </span>
                  </div>
                ))}
              </div>
            )}
            {replayStats && replayStats.tools && replayStats.tools.length > 0 && (
              <ReplayVisual stats={replayStats} />
            )}
          </div>

          <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
            <div className="flex items-center justify-between mb-2">
              <div className="font-medium text-sm">熔断状态</div>
              <button className="text-xs" style={{ color: 'var(--text-dim)' }} onClick={() => resetBreaker()}>
                全部重置
              </button>
            </div>
            {breakers.length === 0 ? (
              <div className="text-xs py-2" style={{ color: 'var(--text-faint)' }}>暂无工具调用记录</div>
            ) : (
              breakers.map((b) => (
                <div key={b.toolName} className="flex items-center gap-2 py-1 text-xs">
                  <span style={{ color: 'var(--text-dim)' }}>{b.toolName}</span>
                  <div className="flex-1 h-1.5 rounded" style={{ background: 'var(--bg-elev)' }}>
                    <div
                      className="h-full rounded"
                      style={{
                        width: `${Math.min(100, (b.callsInWindow / b.threshold) * 100)}%`,
                        background: b.open ? 'var(--danger)' : 'var(--accent)',
                      }}
                    />
                  </div>
                  <span className="font-mono" style={{ color: b.open ? 'var(--danger)' : 'var(--text-faint)' }}>
                    {b.callsInWindow}/{b.threshold}
                  </span>
                  {b.open && (
                    <button className="text-xs" style={{ color: 'var(--accent)' }} onClick={() => resetBreaker(b.toolName)}>
                      重置
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default AuditView
