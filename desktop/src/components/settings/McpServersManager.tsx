import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'

interface McpServer {
  id: string
  name: string
  url: string
  headers: Record<string, string>
  enabled: boolean
  tools_cache?: Array<{ name?: string; description?: string }>
  last_health_at?: number | null
  last_health_ok?: boolean | null
}

const EMPTY_FORM = {
  id: '',
  name: '',
  url: '',
  headers: '',
  enabled: true,
}

/** 外部 MCP Server 管理（动态挂载为 Agent 工具，可插拔） */
const McpServersManager: React.FC = () => {
  const [servers, setServers] = useState<McpServer[]>([])
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await api.mcpServers.list()
      setServers(Array.isArray(list) ? list : [])
    } catch (e) {
      setMsg({ kind: 'err', text: `加载 MCP 服务失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (k: keyof typeof EMPTY_FORM, v: string | boolean) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  const parseHeaders = (): Record<string, string> => {
    const text = form.headers.trim()
    if (!text) return {}
    try {
      return JSON.parse(text)
    } catch {
      throw new Error('请求头不是合法 JSON')
    }
  }

  const submit = async () => {
    setMsg(null)
    if (!form.id.trim() || !form.name.trim() || !form.url.trim()) {
      setMsg({ kind: 'err', text: '请填写 ID、名称、URL' })
      return
    }
    let headers: Record<string, string> = {}
    try {
      headers = parseHeaders()
    } catch (e) {
      setMsg({ kind: 'err', text: (e as Error).message })
      return
    }
    try {
      const payload = { name: form.name, url: form.url.trim(), headers, enabled: form.enabled }
      if (editingId) {
        await api.mcpServers.update(editingId, payload)
        setMsg({ kind: 'ok', text: 'MCP 服务已更新' })
      } else {
        await api.mcpServers.create({ id: form.id, ...payload })
        setMsg({ kind: 'ok', text: 'MCP 服务已新增' })
      }
      setForm({ ...EMPTY_FORM })
      setEditingId(null)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `${editingId ? '更新' : '新增'}失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const remove = async (id: string) => {
    if (!window.confirm(`确认删除 MCP 服务「${id}」？`)) return
    setMsg(null)
    try {
      await api.mcpServers.remove(id)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `删除失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const test = async (id: string) => {
    setBusyId(`test:${id}`)
    setMsg(null)
    try {
      const r = await api.mcpServers.test(id)
      const info = r.serverInfo || {}
      setMsg({ kind: 'ok', text: `连接正常（协议 ${r.protocolVersion || '未知'}，服务 ${info.name || '未知'}）` })
    } catch (e) {
      setMsg({ kind: 'err', text: `连接失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const sync = async (id: string) => {
    setBusyId(`sync:${id}`)
    setMsg(null)
    try {
      const r = await api.mcpServers.sync(id)
      setMsg({ kind: 'ok', text: `已同步 ${r.tool_count || 0} 个工具，下次对话自动挂载` })
    } catch (e) {
      setMsg({ kind: 'err', text: `同步失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const edit = (s: McpServer) => {
    setEditingId(s.id)
    setForm({
      id: s.id,
      name: s.name,
      url: s.url,
      headers: JSON.stringify(s.headers || {}, null, 2),
      enabled: s.enabled,
    })
  }

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">外部 MCP 服务（技能库挂载）</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
        动态挂载外部 MCP Server（HTTP JSON-RPC 端点），其工具会作为 Agent 工具出现——新增服务后点「同步工具」拉取工具清单，下次对话自动生效，无需写死代码。支持任意第三方 MCP 服务（知识库、搜索、数据库、企业系统等）。
      </div>

      {msg && (
        <div className="mb-3 p-2 rounded-lg text-sm" style={{
          background: msg.kind === 'err' ? 'var(--error-soft)' : 'var(--ok-soft)',
          color: msg.kind === 'err' ? 'var(--error)' : 'var(--ok)',
        }}>
          {msg.text}
        </div>
      )}

      {servers.length > 0 && (
        <div className="space-y-2 mb-4">
          {servers.map((s) => {
            const tools = Array.isArray(s.tools_cache) ? s.tools_cache : []
            return (
              <div key={s.id} className="flex items-center justify-between p-3 rounded-lg"
                style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-sm">{s.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                      {tools.length > 0 ? `${tools.length} 个工具` : '未同步工具'}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded" style={{
                      background: s.enabled ? 'var(--ok-soft)' : 'var(--error-soft)',
                      color: s.enabled ? 'var(--ok)' : 'var(--error)',
                    }}>{s.enabled ? '启用' : '停用'}</span>
                    {s.last_health_ok !== null && s.last_health_ok !== undefined && (
                      <span className="text-[10px]" style={{ color: s.last_health_ok ? 'var(--ok)' : 'var(--error)' }}>
                        {s.last_health_ok ? '● 健康' : '○ 异常'}
                      </span>
                    )}
                  </div>
                  <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-faint)' }}>
                    {s.url}
                    {tools.length > 0 && (
                      <span className="ml-2">
                        {tools.slice(0, 3).map((t) => t.name).join('、')}
                        {tools.length > 3 ? '…' : ''}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex gap-1.5 ml-3 shrink-0">
                  <button onClick={() => test(s.id)} disabled={busyId === `test:${s.id}`}
                    className="px-2.5 py-1 text-xs rounded-lg"
                    style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                    {busyId === `test:${s.id}` ? '测试中…' : '测试连接'}
                  </button>
                  <button onClick={() => sync(s.id)} disabled={busyId === `sync:${s.id}`}
                    className="px-2.5 py-1 text-xs rounded-lg"
                    style={{ background: 'var(--primary-soft, #eef2ff)', color: 'var(--primary)', border: '1px solid var(--primary)' }}>
                    {busyId === `sync:${s.id}` ? '同步中…' : '同步工具'}
                  </button>
                  <button onClick={() => edit(s)}
                    className="px-2.5 py-1 text-xs rounded-lg"
                    style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                    编辑
                  </button>
                  <button onClick={() => remove(s.id)}
                    className="px-2.5 py-1 text-xs rounded-lg"
                    style={{ background: 'var(--error-soft)', color: 'var(--error)', border: '1px solid var(--error-soft)' }}>
                    删除
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* 新增/编辑表单 */}
      <div className="space-y-3 p-3 rounded-lg" style={{ background: 'var(--bg-elev)', border: '1px dashed var(--border-soft)' }}>
        <div className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>
          {editingId ? `编辑服务：${editingId}` : '新增 MCP 服务'}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>ID（唯一标识）</label>
            <input type="text" value={form.id} disabled={!!editingId} onChange={(e) => set('id', e.target.value)}
              placeholder="mcp-rag-server"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>显示名称</label>
            <input type="text" value={form.name} onChange={(e) => set('name', e.target.value)}
              placeholder="我的 MCP 服务"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>MCP 端点 URL（HTTP JSON-RPC）</label>
          <input type="text" value={form.url} onChange={(e) => set('url', e.target.value)}
            placeholder="https://your-mcp-server.com/mcp"
            className="w-full mt-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>请求头（JSON，可选，用于鉴权）</label>
          <textarea value={form.headers} onChange={(e) => set('headers', e.target.value)}
            rows={2}
            placeholder='{"Authorization": "Bearer xxx"}'
            className="w-full mt-1 p-2 rounded-lg text-sm font-mono"
            style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-dim)' }}>
            <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
            启用
          </label>
          <div className="flex-1" />
          <button onClick={() => { setForm({ ...EMPTY_FORM }); setEditingId(null) }}
            className="px-3 py-1.5 text-xs rounded-lg"
            style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
            取消
          </button>
          <button onClick={submit}
            className="px-4 py-1.5 rounded-lg text-sm font-medium"
            style={{ background: 'var(--primary)', color: 'white', border: '1px solid var(--primary)' }}>
            {editingId ? '保存修改' : '+ 新增服务'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default McpServersManager
