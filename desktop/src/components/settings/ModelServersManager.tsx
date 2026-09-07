import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'

interface LlmServer {
  id: string
  name: string
  base_url: string
  protocol: string
  enabled: boolean
  timeout_sec: number
  has_api_key: boolean
  last_health_at?: number | null
  last_health_ok?: boolean | null
  models_cache?: string[]
  allowed_models?: string[]
}

const EMPTY_FORM = {
  id: '',
  name: '',
  base_url: '',
  protocol: 'ollama',
  api_key: '',
  timeout_sec: 60,
  enabled: true,
  allowed_models: '',
}

/** 把逗号分隔的模型名称字符串解析为数组（去空、去重）。 */
const parseAllowed = (str: string): string[] => {
  const parts = str.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
  return Array.from(new Set(parts))
}

/** 模型服务器连接管理（局域网/互联网大模型服务器：Ollama、OpenAI 兼容网关等） */
const ModelServersManager: React.FC = () => {
  const [servers, setServers] = useState<LlmServer[]>([])
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)
  const [showApiKey, setShowApiKey] = useState(false)
  const [copiedField, setCopiedField] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await api.llmServers.list()
      setServers(Array.isArray(list) ? list : [])
    } catch (e) {
      setMsg({ kind: 'err', text: `加载模型服务器失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (k: keyof typeof EMPTY_FORM, v: string | number | boolean) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  const submit = async () => {
    setMsg(null)
    if (!form.id.trim() || !form.name.trim() || !form.base_url.trim()) {
      setMsg({ kind: 'err', text: '请填写 ID、名称、Base URL' })
      return
    }
    try {
      const allowed = parseAllowed(form.allowed_models)
      if (editingId) {
        await api.llmServers.update(editingId, {
          name: form.name,
          base_url: form.base_url,
          protocol: form.protocol,
          api_key: form.api_key || undefined,
          timeout_sec: Number(form.timeout_sec) || 60,
          enabled: form.enabled,
          allowed_models: allowed,
        })
        setMsg({ kind: 'ok', text: '模型服务器已更新' })
      } else {
        await api.llmServers.create({
          id: form.id,
          name: form.name,
          base_url: form.base_url,
          protocol: form.protocol,
          api_key: form.api_key,
          timeout_sec: Number(form.timeout_sec) || 60,
          enabled: form.enabled,
          allowed_models: allowed,
        })
        setMsg({ kind: 'ok', text: '模型服务器已新增' })
      }
      setForm({ ...EMPTY_FORM })
      setEditingId(null)
      setShowApiKey(false)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `${editingId ? '更新' : '新增'}失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const remove = async (id: string) => {
    if (!window.confirm(`确认删除模型服务器「${id}」？`)) return
    setMsg(null)
    try {
      await api.llmServers.remove(id)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `删除失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const test = async (id: string) => {
    setBusyId(id)
    setMsg(null)
    try {
      const r = await api.llmServers.test(id)
      setMsg(r.ok
        ? { kind: 'ok', text: `连接正常：${r.model_count ?? 0} 个模型，延迟 ${r.latency_ms ?? '-'}ms` }
        : { kind: 'err', text: `连接失败：${r.error || '未知错误'}` })
    } catch (e) {
      setMsg({ kind: 'err', text: `测试失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const syncModels = async (id: string) => {
    setBusyId(id)
    setMsg(null)
    try {
      const r = await api.llmServers.syncModels(id)
      setMsg(r.ok
        ? { kind: 'ok', text: `模型同步完成：${r.count ?? 0} 个模型（${(r.models || []).slice(0, 6).join(', ')}${(r.models || []).length > 6 ? '…' : ''}）` }
        : { kind: 'err', text: `同步失败：${r.detail || '未知错误'}` })
    } catch (e) {
      setMsg({ kind: 'err', text: `同步失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const edit = async (s: LlmServer) => {
    setEditingId(s.id)
    setShowApiKey(false)
    setForm({
      id: s.id,
      name: s.name,
      base_url: s.base_url,
      protocol: s.protocol,
      api_key: '',
      timeout_sec: s.timeout_sec,
      enabled: s.enabled,
      allowed_models: (s.allowed_models || []).join(', '),
    })
    // 编辑时拉取真实 API Key 回填
    if (s.has_api_key) {
      try {
        const r = await api.llmServers.getApiKey(s.id)
        if (r?.api_key) {
          setForm((prev) => ({ ...prev, api_key: r.api_key }))
        }
      } catch {
        // 拉取失败不阻塞编辑，用户可手动重填
      }
    }
  }

  const copyToClipboard = async (text: string, field: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedField(field)
      setTimeout(() => setCopiedField(null), 1500)
    } catch {
      setMsg({ kind: 'err', text: '复制失败，请手动选择复制' })
    }
  }

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">模型服务器连接</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
        局域网 / 互联网的大模型服务器（Ollama、OpenAI 兼容网关等）。本客户端不再内置本地模型，对话模型全部来自以下连接。
      </div>

      {msg && (
        <div className="mb-3 p-2 rounded-lg text-sm" style={{
          background: msg.kind === 'err' ? 'var(--error-soft)' : 'var(--ok-soft)',
          color: msg.kind === 'err' ? 'var(--error)' : 'var(--ok)',
        }}>
          {msg.text}
        </div>
      )}

      {/* 已配置列表 */}
      {servers.length > 0 && (
        <div className="space-y-2 mb-4">
          {servers.map((s) => (
            <div key={s.id} className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-sm">{s.name}</span>
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
                  {s.protocol} · {s.base_url} · {s.has_api_key ? '已配密钥' : '无鉴权'}
                </div>
                {s.models_cache && s.models_cache.length > 0 && (
                  <div className="text-[10px] mt-0.5 truncate" style={{ color: 'var(--text-faint)' }}>
                    模型：{s.models_cache.join(', ')}
                  </div>
                )}
              </div>
              <div className="flex gap-1.5 ml-3 shrink-0">
                <button onClick={() => test(s.id)} disabled={busyId === s.id}
                  className="px-2.5 py-1 text-xs rounded-lg"
                  style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  {busyId === s.id ? '测试中…' : '测试'}
                </button>
                <button onClick={() => syncModels(s.id)} disabled={busyId === s.id}
                  className="px-2.5 py-1 text-xs rounded-lg"
                  style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  同步模型
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
          {editingId ? `编辑连接：${editingId}` : '新增模型服务器连接'}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>ID（唯一标识）</label>
            <input type="text" value={form.id} disabled={!!editingId} onChange={(e) => set('id', e.target.value)}
              placeholder="ollama-office"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>显示名称</label>
            <input type="text" value={form.name} onChange={(e) => set('name', e.target.value)}
              placeholder="办公室 Ollama 服务器"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>Base URL</label>
          <div style={{ position: 'relative' }}>
            <input type="text" value={form.base_url} onChange={(e) => set('base_url', e.target.value)}
              placeholder="http://192.168.1.100:11434 或 http://api.example.com"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)', paddingRight: '70px' }} />
            <button type="button" onClick={() => copyToClipboard(form.base_url, 'base_url')}
              className="absolute top-1/2 -translate-y-1/2 right-2 px-2 py-1 text-xs rounded"
              style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: copiedField === 'base_url' ? 'var(--ok)' : 'var(--text-dim)' }}>
              {copiedField === 'base_url' ? '已复制' : '复制'}
            </button>
          </div>
          <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
            局域网填内网地址（如 http://192.168.1.100:11434），互联网填公网地址；无需写 /v1，系统自动补齐
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>协议</label>
            <select value={form.protocol} onChange={(e) => set('protocol', e.target.value)}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}>
              <option value="ollama">Ollama（原生）</option>
              <option value="openai">OpenAI 兼容</option>
            </select>
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>超时（秒）</label>
            <input type="number" value={form.timeout_sec} onChange={(e) => set('timeout_sec', Number(e.target.value))}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>API Key（可选）</label>
          <div style={{ position: 'relative' }}>
            <input type={showApiKey ? 'text' : 'password'} value={form.api_key} onChange={(e) => set('api_key', e.target.value)}
              placeholder="sk-...（无鉴权留空；编辑时留空表示不修改）"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)', paddingRight: '90px', fontFamily: 'monospace' }} />
            <div style={{ position: 'absolute', top: '50%', right: '6px', transform: 'translateY(calc(-50% + 4px))', display: 'flex', gap: '4px' }}>
              {form.api_key && (
                <button type="button" onClick={() => copyToClipboard(form.api_key, 'api_key')}
                  className="px-2 py-1 text-xs rounded"
                  style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: copiedField === 'api_key' ? 'var(--ok)' : 'var(--text-dim)' }}>
                  {copiedField === 'api_key' ? '已复制' : '复制'}
                </button>
              )}
              <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                className="px-2 py-1 text-xs rounded"
                style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text-dim)' }}>
                {showApiKey ? '隐藏' : '查看'}
              </button>
            </div>
          </div>
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>模型名称（白名单）</label>
          <input type="text" value={form.allowed_models} onChange={(e) => set('allowed_models', e.target.value)}
            placeholder="agnes-2.5-flash, agnes-2.5-pro（留空同步全部模型）"
            className="w-full mt-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)', fontFamily: 'monospace' }} />
          <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
            仅同步列表内的模型，多个用英文逗号分隔；留空表示不限制，同步服务器上全部模型
          </div>
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-dim)' }}>
            <input type="checkbox" checked={form.enabled} onChange={(e) => set('enabled', e.target.checked)} />
            启用
          </label>
          <div className="flex-1" />
          <button onClick={() => { setForm({ ...EMPTY_FORM }); setEditingId(null); setShowApiKey(false) }}
            className="px-3 py-1.5 text-xs rounded-lg"
            style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
            取消
          </button>
          <button onClick={submit}
            className="px-4 py-1.5 rounded-lg text-sm font-medium"
            style={{ background: 'var(--primary)', color: 'white', border: '1px solid var(--primary)' }}>
            {editingId ? '保存修改' : '+ 新增连接'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ModelServersManager
