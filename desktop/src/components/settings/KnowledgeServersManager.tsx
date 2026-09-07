import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'

interface KbServer {
  id: string
  name: string
  type: string
  platform: string
  base_url: string
  auth_type: string
  enabled: boolean
  timeout_sec: number
  has_api_key: boolean
  extra_config?: Record<string, unknown>
  last_health_at?: number | null
  last_health_ok?: boolean | null
}

interface PlatformOption {
  platform: string
  implemented: boolean
}

const EMPTY_FORM = {
  id: '',
  name: '',
  type: 'rag',
  platform: 'dify',
  base_url: '',
  auth_type: 'api_key',
  api_key: '',
  extra_config: '',
  timeout_sec: 30,
  enabled: true,
}

const TYPE_LABELS: Record<string, string> = {
  rag: 'RAG 检索',
  wiki: 'LLM Wiki',
  generic: '通用检索',
}

const PLATFORM_LABELS: Record<string, string> = {
  dify: 'Dify',
  confluence: 'Confluence',
  generic: '通用 HTTP',
  fastgpt: 'FastGPT',
  ragflow: 'RAGFlow',
  notion: 'Notion',
  wiki_js: 'Wiki.js',
  feishu_wiki: '飞书知识库',
}

/** 知识库连接管理（局域网/互联网 RAG、LLM Wiki 等） */
const KnowledgeServersManager: React.FC = () => {
  const [servers, setServers] = useState<KbServer[]>([])
  const [platforms, setPlatforms] = useState<PlatformOption[]>([])
  const [form, setForm] = useState({ ...EMPTY_FORM })
  const [editingId, setEditingId] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const [list, plats] = await Promise.all([
        api.knowledgeServers.list(),
        api.knowledgeServers.platforms(),
      ])
      setServers(Array.isArray(list) ? list : [])
      setPlatforms(Array.isArray(plats) ? plats : [])
    } catch (e) {
      setMsg({ kind: 'err', text: `加载知识库连接失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const set = (k: keyof typeof EMPTY_FORM, v: string | number | boolean) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  const parseExtra = (): Record<string, unknown> => {
    try {
      const text = form.extra_config.trim()
      return text ? JSON.parse(text) : {}
    } catch {
      throw new Error('高级配置不是合法 JSON')
    }
  }

  const submit = async () => {
    setMsg(null)
    if (!form.id.trim() || !form.name.trim() || !form.base_url.trim()) {
      setMsg({ kind: 'err', text: '请填写 ID、名称、Base URL' })
      return
    }
    let extra: Record<string, unknown> = {}
    try {
      extra = parseExtra()
    } catch (e) {
      setMsg({ kind: 'err', text: (e as Error).message })
      return
    }
    try {
      const payload = {
        name: form.name,
        type: form.type,
        platform: form.platform,
        base_url: form.base_url,
        auth_type: form.auth_type,
        api_key: form.api_key || undefined,
        extra_config: extra,
        timeout_sec: Number(form.timeout_sec) || 30,
        enabled: form.enabled,
      }
      if (editingId) {
        await api.knowledgeServers.update(editingId, payload)
        setMsg({ kind: 'ok', text: '知识库连接已更新' })
      } else {
        await api.knowledgeServers.create({ id: form.id, ...payload })
        setMsg({ kind: 'ok', text: '知识库连接已新增' })
      }
      setForm({ ...EMPTY_FORM })
      setEditingId(null)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `${editingId ? '更新' : '新增'}失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const remove = async (id: string) => {
    if (!window.confirm(`确认删除知识库连接「${id}」？`)) return
    setMsg(null)
    try {
      await api.knowledgeServers.remove(id)
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `删除失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const test = async (id: string) => {
    setBusyId(id)
    setMsg(null)
    try {
      const r = await api.knowledgeServers.test(id)
      setMsg(r.ok
        ? { kind: 'ok', text: '连接正常' }
        : { kind: 'err', text: `连接失败：${r.error || '未知错误'}` })
    } catch (e) {
      setMsg({ kind: 'err', text: `测试失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setBusyId(null)
      await load()
    }
  }

  const edit = (s: KbServer) => {
    setEditingId(s.id)
    setForm({
      id: s.id,
      name: s.name,
      type: s.type,
      platform: s.platform,
      base_url: s.base_url,
      auth_type: s.auth_type,
      api_key: '',
      extra_config: JSON.stringify(s.extra_config || {}, null, 2),
      timeout_sec: s.timeout_sec,
      enabled: s.enabled,
    })
  }

  const implemented = (platform: string) =>
    platforms.find((p) => p.platform === platform)?.implemented ?? true

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">知识库连接</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
        局域网 / 互联网的知识库（RAG 检索服务、LLM Wiki 等），支持新增、编辑、测试、删除；对话中的知识检索工具将使用以下连接。
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
                  <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                    {TYPE_LABELS[s.type] || s.type} · {PLATFORM_LABELS[s.platform] || s.platform}
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
                  {s.base_url} · {s.has_api_key ? '已配密钥' : '无鉴权'}
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
          {editingId ? `编辑连接：${editingId}` : '新增知识库连接'}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>ID（唯一标识）</label>
            <input type="text" value={form.id} disabled={!!editingId} onChange={(e) => set('id', e.target.value)}
              placeholder="kb-rag-prod"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>显示名称</label>
            <input type="text" value={form.name} onChange={(e) => set('name', e.target.value)}
              placeholder="生产 RAG 知识库"
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>类型</label>
            <select value={form.type} onChange={(e) => set('type', e.target.value)}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}>
              <option value="rag">RAG 检索</option>
              <option value="wiki">LLM Wiki</option>
              <option value="generic">通用检索</option>
            </select>
          </div>
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>平台</label>
            <select value={form.platform} onChange={(e) => set('platform', e.target.value)}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}>
              {platforms.map((p) => (
                <option key={p.platform} value={p.platform}>
                  {PLATFORM_LABELS[p.platform] || p.platform}{p.implemented ? '' : '（待接入）'}
                </option>
              ))}
            </select>
            {!implemented(form.platform) && (
              <div className="text-xs mt-1" style={{ color: 'var(--error)' }}>
                该平台适配器尚未实现，可使用「通用检索」或先保存连接
              </div>
            )}
          </div>
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>Base URL</label>
          <input type="text" value={form.base_url} onChange={(e) => set('base_url', e.target.value)}
            placeholder="http://192.168.1.101 或 https://wiki.example.com"
            className="w-full mt-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm" style={{ color: 'var(--text-dim)' }}>鉴权方式</label>
            <select value={form.auth_type} onChange={(e) => set('auth_type', e.target.value)}
              className="w-full mt-1 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}>
              <option value="api_key">API Key</option>
              <option value="token">Token</option>
              <option value="basic">Basic 账号:密码</option>
              <option value="none">无鉴权</option>
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
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>密钥（可选）</label>
          <input type="password" value={form.api_key} onChange={(e) => set('api_key', e.target.value)}
            placeholder="API Key / Token / user:pass（无鉴权留空）"
            className="w-full mt-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-panel)', color: 'var(--text)', border: '1px solid var(--border-soft)' }} />
        </div>
        <div>
          <label className="text-sm" style={{ color: 'var(--text-dim)' }}>高级配置（JSON，可选）</label>
          <textarea value={form.extra_config} onChange={(e) => set('extra_config', e.target.value)}
            rows={3}
            placeholder={'Dify: {"dataset_id": "..."}\nConfluence: {"space": "KEY"}\n通用: {"method": "POST", "path": "/search", "results_path": "data.items", "title_field": "title", "snippet_field": "snippet"}'}
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
            {editingId ? '保存修改' : '+ 新增连接'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default KnowledgeServersManager
