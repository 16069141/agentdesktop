import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'

interface SearchServer {
  id: string
  name: string
  provider: string
  base_url: string | null
  enabled: boolean
  max_results: number
  timeout_sec: number
  has_api_key: boolean
  last_health_at?: number | null
  last_health_ok?: boolean | null
}

const EMPTY_FORM = {
  id: '',
  name: '',
  provider: 'bing_web',
  base_url: '',
  api_key: '',
  max_results: 5,
  timeout_sec: 15,
  enabled: true,
}

const PROVIDER_LABELS: Record<string, string> = {
  bing_web: 'Bing 网页版（免 Key）',
  duckduckgo: 'DuckDuckGo（免 Key）',
  tavily: 'Tavily（需 Key）',
  bing: 'Bing Search API（需 Key）',
  brave: 'Brave Search（需 Key）',
  serpapi: 'SerpApi（需 Key）',
}

/** 联网搜索服务管理（web_search 工具 provider 配置） */
const SearchServersManager: React.FC = () => {
  const [servers, setServers] = useState<SearchServer[]>([])
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)
  const [showApiKey, setShowApiKey] = useState(true)  // 编辑时默认明文显示已保存的 key

  const load = useCallback(async () => {
    try {
      const list = await api.webSearchServers.list()
      setServers(Array.isArray(list) ? list : [])
    } catch (e) {
      setMsg({ kind: 'err', text: `加载搜索服务失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (k: keyof typeof EMPTY_FORM, v: string | number | boolean) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  const submit = async () => {
    setMsg(null)
    if (!form.id.trim() || !form.name.trim()) {
      setMsg({ kind: 'err', text: '请填写 ID 与名称' })
      return
    }
    try {
      const payload = {
        name: form.name,
        provider: form.provider,
        base_url: form.base_url.trim() || undefined,
        api_key: form.api_key || undefined,
        max_results: Number(form.max_results) || 5,
        timeout_sec: Number(form.timeout_sec) || 15,
        enabled: form.enabled,
      }
      if (editingId) {
        await api.webSearchServers.update(editingId, payload)
        setMsg({ kind: 'ok', text: '搜索服务已更新' })
      } else {
        await api.webSearchServers.create({ id: form.id, ...payload })
        setMsg({ kind: 'ok', text: '搜索服务已新增' })
      }
      setForm({ ...EMPTY_FORM })
      setEditingId(null)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `${editingId ? '更新' : '新增'}失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const remove = async (id: string) => {
    if (!window.confirm(`确认删除搜索服务「${id}」？`)) return
    setMsg(null)
    try {
      await api.webSearchServers.remove(id)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `删除失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const test = async (id: string) => {
    setBusyId(id)
    setMsg(null)
    try {
      const r = await api.webSearchServers.test(id)
      setMsg(r.ok
        ? { kind: 'ok', text: `搜索正常（${r.provider}，返回 ${r.count} 条）` }
        : { kind: 'err', text: `搜索失败：${r.error || '未知错误'}` })
    } catch (e) {
      setMsg({ kind: 'err', text: `测试失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const edit = async (s: SearchServer) => {
    setEditingId(s.id)
    setForm({
      id: s.id,
      name: s.name,
      provider: s.provider,
      base_url: s.base_url || '',
      api_key: '',
      max_results: s.max_results,
      timeout_sec: s.timeout_sec,
      enabled: s.enabled,
    })
    // 编辑时回填已保存的 API Key（后端明文接口，仅本地回环可访问）
    try {
      const r = await api.webSearchServers.getApiKey(s.id)
      if (r && r.api_key) {
        setForm((f) => ({ ...f, api_key: r.api_key }))
        setShowApiKey(true)
      }
    } catch { /* key 拉取失败保持留空，不影响编辑 */ }
  }

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">联网搜索服务</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
        配置 web_search 工具的搜索提供商。未配置任何服务时，对话会自动使用 Bing 网页版（免 Key）兜底；列表第一个启用的服务作为默认搜索。
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
          {servers.map((s) => (
            <div key={s.id} className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{s.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                    {PROVIDER_LABELS[s.provider] || s.provider}
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
                  {s.base_url || PROVIDER_LABELS[s.provider] || s.provider} · {s.has_api_key ? '已配 Key' : '免 Key'}
                </div>
              </div>
              <div className="flex gap-1.5 ml-3 shrink-0">
                <button onClick={() => test(s.id)} disabled={busyId === s.id}
                  className="px-2.5 py-1 text-xs rounded-lg"
                  style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  {busyId === s.id ? '测试中…' : '测试'}
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
          ))}
        </div>
      )}

      {/* 新增/编辑表单 */}
      <div className="space-y-3 p-3 rounded-lg" style={{ background: 'var(--bg-elev)', border: '1px dashed var(--border-soft)' }}>
        <div className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>
          {editingId ? `编辑服务：${editingId}` : '新增搜索服务'}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>ID（唯一标识）</label>
            <input type="text" value={form.id} disabled={!!editingId} onChange={(e) => set('id', e.target.value)}
              placeholder="tavily-prod"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>显示名称</label>
            <input type="text" value={form.name} onChange={(e) => set('name', e.target.value)}
              placeholder="Tavily 生产搜索"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>提供商</label>
            <select value={form.provider} onChange={(e) => set('provider', e.target.value)}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}>
              {Object.entries(PROVIDER_LABELS).map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>Base URL（可选，留空用默认）</label>
            <input type="text" value={form.base_url} onChange={(e) => set('base_url', e.target.value)}
              placeholder="自定义 API 端点（一般留空）"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>API Key（免 Key 提供商可留空）</label>
            <div className="relative mt-1">
              <input type={showApiKey ? 'text' : 'password'} value={form.api_key} onChange={(e) => set('api_key', e.target.value)}
                placeholder="Tavily / Bing / Brave / SerpApi Key"
                className="w-full p-2 pr-16 rounded-lg text-sm"
                style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
              <button type="button" onClick={() => setShowApiKey((v) => !v)} title={showApiKey ? '隐藏' : '显示'} className="absolute top-1/2 -translate-y-1/2 right-8 text-sm" style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer' }}>
                {showApiKey ? '🙈' : '👁'}
              </button>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-sm" style={{ color: 'var(--text-dim)' }}>最大结果数</label>
              <input type="number" value={form.max_results} onChange={(e) => set('max_results', Number(e.target.value))}
                className="w-full mt-1 p-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
            </div>
            <div>
              <label className="text-sm" style={{ color: 'var(--text-dim)' }}>超时（秒）</label>
              <input type="number" value={form.timeout_sec} onChange={(e) => set('timeout_sec', Number(e.target.value))}
                className="w-full mt-1 p-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
            </div>
          </div>
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

export default SearchServersManager
