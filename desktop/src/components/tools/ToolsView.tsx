import React, { useState, useEffect } from 'react'
import { api } from '../../api'

interface ToolItem {
  id: string
  name: string
  description: string
  source: string
  enabled: boolean
  requiresApproval: boolean
  lastLoadedAt?: number
  lastUsedAt?: number
}

const ToolsView: React.FC = () => {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const loadTools = async () => {
    try {
      setLoading(true)
      const data = await api.tools.list()
      setTools(data)
    } catch (e) {
      setError(`加载工具列表失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTools()
  }, [])

  const handleToggle = async (tool: ToolItem) => {
    try {
      if (tool.enabled) {
        await api.tools.disable(tool.id)
      } else {
        await api.tools.enable(tool.id)
      }
      await loadTools()
      setSuccess(`${tool.name} ${tool.enabled ? '已禁用' : '已启用'}`)
    } catch (e) {
      setError(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const formatTime = (ts?: number) => {
    if (!ts) return '-'
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  }

  return (
    <div className="p-6" style={{ color: 'var(--text)' }}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-lg font-semibold">工具管理</h2>
          <div className="text-sm mt-1" style={{ color: 'var(--text-dim)' }}>
            MCP 工具集启停 · 来源白名单 · 审计日志（Phase 2）
          </div>
        </div>
        <button
          onClick={loadTools}
          className="px-3 py-1.5 rounded-lg text-sm"
          style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
        >
          刷新
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}
      {success && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--ok-soft)', color: 'var(--ok)' }}>
          {success}
        </div>
      )}

      {loading ? (
        <div className="text-center py-8" style={{ color: 'var(--text-faint)' }}>加载中...</div>
      ) : (
        <div className="space-y-3">
          {tools.map((tool) => (
            <div
              key={tool.id}
              className="p-4 rounded-xl"
              style={{
                background: 'var(--bg-panel)',
                border: `1px solid ${tool.enabled ? 'var(--border-soft)' : 'var(--border-soft)'}`,
                opacity: tool.enabled ? 1 : 0.7,
              }}
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium">{tool.name}</span>
                    <span
                      className="px-2 py-0.5 rounded text-xs"
                      style={tool.enabled
                        ? { background: 'var(--ok-soft)', color: 'var(--ok)' }
                        : { background: 'var(--bg-elev)', color: 'var(--text-faint)' }
                      }
                    >
                      {tool.enabled ? '已启用' : '已禁用'}
                    </span>
                    {tool.source === 'builtin' && (
                      <span
                        className="px-2 py-0.5 rounded text-xs"
                        style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}
                      >
                        内置
                      </span>
                    )}
                  </div>
                  <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{tool.description}</div>
                  <div className="text-xs mt-2" style={{ color: 'var(--text-faint)' }}>
                    {tool.requiresApproval && '🔒 需用户确认 · '}
                    来源：{tool.source} · 最后加载：{formatTime(tool.lastLoadedAt)} · 最后使用：{formatTime(tool.lastUsedAt)}
                  </div>
                </div>
                <button
                  onClick={() => handleToggle(tool)}
                  disabled={tool.source === 'builtin'}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-opacity ${tool.source === 'builtin' ? 'opacity-50 cursor-not-allowed' : 'hover:opacity-90'}`}
                  style={tool.enabled
                    ? { background: 'var(--error-soft)', color: 'var(--error)' }
                    : { background: 'var(--ok-soft)', color: 'var(--ok)' }
                  }
                >
                  {tool.enabled ? '禁用' : '启用'}
                </button>
              </div>
            </div>
          ))}

          {tools.length === 0 && (
            <div className="text-center py-8" style={{ color: 'var(--text-faint)' }}>
              暂无工具
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default ToolsView
