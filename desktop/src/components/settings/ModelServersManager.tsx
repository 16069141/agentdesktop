import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'
import { useUiStore } from '../../store/useUiStore'

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

const ModelServersManager: React.FC = () => {
  const [servers, setServers] = useState<LlmServer[]>([])
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)
  const [showApiKey] = useState(false)  // 仅密码框显示模式（无切换入口，保持密码）
  const [copiedField, setCopiedField] = useState<string | null>(null)
  const [formError, setFormError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await api.llmServers.list()
      setServers(Array.isArray(list) ? list : [])
    } catch (e) {
      setMsg({ kind: 'err', text: `加载模型服务器失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  const refreshGlobalModels = useCallback(async () => {
    try {
      const res = await api.models.refresh()
      const models = Array.isArray(res) ? res : ((res as any)?.models ?? [])
      if (Array.isArray(models)) {
        useUiStore.getState().setModels(models)
        const currentModelId = useUiStore.getState().currentModelId
        if (!models.some((m) => m.id === currentModelId)) {
          useUiStore.getState().setCurrentModelId(models[0]?.id ?? '')
        }
      }
    } catch (e) {
      console.warn('[ModelServers] 全局模型列表刷新失败:', e)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (k: keyof typeof EMPTY_FORM, v: string | number | boolean) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  const subm = async (doUpdate: boolean) => {
    setMsg(null)
    setFormError(null)
    if (!form.name.trim()) { setFormError('显示名称不能为空'); return }
    if (!form.base_url.trim()) { setFormError('Base URL 不能为空'); return }
    try {
      setBusyId('form')
      if (doUpdate && editingId) {
        await api.llmServers.update(editingId, form)
        setMsg({ kind: 'ok', text: '服务器已更新' })
      } else {
        await api.llmServers.create(form)
        setMsg({ kind: 'ok', text: '服务器已添加' })
      }
      reset()
      await Promise.all([load(), refreshGlobalModels()])
    } catch (e) {
      setFormError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyId(null)
    }
  }

  const reset = () => {
    setForm({ ...EMPTY_FORM })
    setEditingId(null)
    setFormError(null)
  }

  const edit = (s: LlmServer) => {
    setForm({ id: s.id, name: s.name, base_url: s.base_url, protocol: s.protocol, api_key: '', timeout_sec: s.timeout_sec, enabled: s.enabled, allowed_models: (s.allowed_models ?? []).join(',') })
    setEditingId(s.id)
    setMsg(null)
    setFormError(null)
  }

  const toggle = async (s: LlmServer) => {
    try { await api.llmServers.update(s.id, { ...s, enabled: !s.enabled }) ; await load() }
    catch { /* ignore */ }
  }

  const remove = async (s: LlmServer) => {
    try { await api.llmServers.delete(s.id) ; await load() ; await refreshGlobalModels() }
    catch { /* ignore */ }
  }

  const probe = async (s: LlmServer) => {
    setBusyId(s.id);
    try {
      const r = await api.llmServers.healthCheck(s.id);
      const models = (r as any)?.models ?? [];
      setServers((list) => list.map((x) => x.id === s.id ? { ...x, last_health_at: Date.now() / 1000, last_health_ok: !!r, models_cache: Array.isArray(models) ? models : [] } : x));
      await refreshGlobalModels();
    } catch {
      setServers((list) => list.map((x) => x.id === s.id ? { ...x, last_health_ok: false, last_health_at: Date.now() / 1000 } : x));
    } finally { setBusyId(null); }
  }

  const copyToClipboard = async (text: string, field: string) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopiedField(field)
      setTimeout(() => setCopiedField(null), 2000)
    } catch { /* ignore */ }
  }

  const formatTime = (ts?: number | null) => ts ? new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''

  return (
    <div className="space-y-6 max-w-3xl">
      {msg && (
        <div className={`rounded-xl p-4 ${msg.kind === 'err' ? 'neu-inset text-red-400' : 'neu-inset text-green-400'}`} style={{ color: msg.kind === 'err' ? 'var(--danger)' : 'var(--ok)' }}>
          {msg.text}
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">模型服务器</h2>
        <button className="neu-btn-primary" onClick={() => subm(false)} disabled={!!busyId}>
          + 添加服务器
        </button>
      </div>

      {/* 表单弹窗 */}
      {(editingId || form.name || form.base_url) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 neu-modal-overlay">
          <div className="neu-modal w-full max-w-lg p-6 space-y-5">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
                {editingId ? '编辑服务器' : '添加服务器'}
              </h3>
              <button onClick={reset} className="text-2xl leading-none opacity-50 hover:opacity-100" style={{ color: 'var(--text-dim)' }}>×</button>
            </div>

            <div className="neu-inset p-4 space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="neu-label">协议</label>
                  <select value={form.protocol} onChange={(e) => set('protocol', e.target.value)} className="neu-input w-full">
                    <option value="ollama">Ollama（原生）</option>
                    <option value="openai">OpenAI 兼容网关</option>
                  </select>
                </div>
                <div>
                  <label className="neu-label">显示名称</label>
                  <input type="text" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="办公室 Ollama 服务器" className="neu-input w-full" />
                </div>
              </div>

              <div>
                <label className="neu-label">Base URL</label>
                <div style={{ position: 'relative' }}>
                  <input type="text" value={form.base_url} onChange={(e) => set('base_url', e.target.value)} placeholder="http://192.168.1.100:11434" className="neu-input w-full" style={{ paddingRight: '70px' }} />
                  <button type="button" onClick={() => copyToClipboard(form.base_url, 'base_url')} className="absolute top-1/2 -translate-y-1/2 right-2 px-2 py-1 text-xs rounded" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: copiedField === 'base_url' ? 'var(--ok)' : 'var(--text-dim)' }}>
                    {copiedField === 'base_url' ? '已复制' : '复制'}
                  </button>
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
                  局域网填内网地址；互联网填公网地址；无需写 /v1，系统自动补齐
                </div>
              </div>

              <div>
                <label className="neu-label">API Key（如有）</label>
                <div style={{ position: 'relative' }}>
                  <input type={showApiKey ? 'text' : 'password'} value={form.api_key} onChange={(e) => set('api_key', e.target.value)} placeholder="留空则不发送" className="neu-input w-full pr-16" />
                  <button type="button" onClick={() => copyToClipboard(form.api_key, 'api_key')} className="absolute top-1/2 -translate-y-1/2 right-2 px-2 py-1 text-xs rounded" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: copiedField === 'api_key' ? 'var(--ok)' : 'var(--text-dim)' }}>
                    {copiedField === 'api_key' ? '已复制' : '复制'}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="neu-label">超时（秒）</label>
                  <input type="number" value={form.timeout_sec} onChange={(e) => set('timeout_sec', Number(e.target.value))} className="neu-input w-full" min={10} max={300} />
                </div>
                <div>
                  <label className="neu-label">允许模型（逗号分隔）</label>
                  <input type="text" value={form.allowed_models} onChange={(e) => set('allowed_models', e.target.value)} placeholder="留空 = 全部可见" className="neu-input w-full" />
                </div>
              </div>

              {formError && (
                <div className="rounded-lg p-3 text-sm" style={{ background: 'rgba(242, 100, 124, 0.12)', color: 'var(--danger)', border: '1px solid rgba(242, 100, 124, 0.25)' }}>
                  {formError}
                </div>
              )}
            </div>

            <div className="flex items-center justify-end gap-3">
              <button className="neu-btn-secondary" onClick={reset} disabled={!!busyId}>取消</button>
              <button className="neu-btn-primary" onClick={() => subm(!!editingId)} disabled={!!busyId}>
                {busyId === 'form' ? '保存中...' : editingId ? '保存更改' : '确认添加'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 服务器列表 */}
      <div className="space-y-3">
        {servers.length === 0 ? (
          <div className="neu-card p-8 text-center" style={{ color: 'var(--text-faint)' }}>
            暂无模型服务器，点击「+ 添加服务器」开始配置
          </div>
        ) : (
          servers.map((s) => (
            <div key={s.id} className="server-item">
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-base" style={{ color: 'var(--text)' }}>{s.name}</span>
                    <span className="px-2 py-0.5 rounded-full text-xs" style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)', border: '1px solid var(--border-soft)' }}>
                      {s.protocol.toUpperCase()}
                    </span>
                    <span className={`neu-status ${s.enabled ? 'neu-status-ok' : 'neu-status-warn'}`}>
                      {s.enabled ? '已启用' : '已停用'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-faint)' }}>
                    <code className="px-2 py-0.5 rounded" style={{ background: 'var(--bg)', fontSize: '12px' }}>{s.base_url}</code>
                    {s.has_api_key && <span style={{ color: 'var(--ok)' }}>🔑</span>}
                    {s.last_health_at && <span>上次检查 {formatTime(s.last_health_at)}</span>}
                    {s.last_health_ok === false && <span style={{ color: 'var(--danger)' }}>• 连接失败</span>}
                    {s.last_health_ok === true && <span style={{ color: 'var(--ok)' }}>• 连接正常</span>}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  <button className="neu-btn-secondary" onClick={() => toggle(s)} title={s.enabled ? '停用' : '启用'}>
                    {s.enabled ? '停用' : '启用'}
                  </button>
                  <button className="neu-btn-secondary" onClick={() => edit(s)} disabled={!!busyId}>
                    编辑
                  </button>
                  <button className="neu-btn-secondary" onClick={() => probe(s)} disabled={!!busyId || busyId === s.id} style={{ minWidth: '60px' }}>
                    {busyId === s.id ? '检测中' : '检测'}
                  </button>
                  <button className="neu-btn-danger" onClick={() => remove(s)} disabled={!!busyId}>
                    删除
                  </button>
                </div>
              </div>
              {Array.isArray(s.allowed_models) && s.allowed_models.length > 0 && (
                <div className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
                  白名单模型：{s.allowed_models.join(', ')}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <div className="neu-divider" />
      <div className="text-xs" style={{ color: 'var(--text-faint)' }}>
        提示：添加/修改服务器后，请手动点击「检测」验证连接，或等待系统定时检测更新状态。
      </div>
    </div>
  )
}

export default ModelServersManager
