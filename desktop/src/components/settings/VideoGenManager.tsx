import React, { useEffect, useState } from 'react'
import { api } from '../../api'

/** P0.8 视频生成服务配置：异步任务式视频端点（OpenAI 风格 / 火山方舟 Seedance 自动探测） */
const VideoGenManager: React.FC = () => {
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [hasKey, setHasKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    api.settings.get().then((s: any) => {
      const v = s?.video_api || {}
      setBaseUrl(v.base_url || '')
      setModel(v.model || '')
      setHasKey(Boolean(v.has_key))
    }).catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const payload: Record<string, unknown> = {
        video_api: { base_url: baseUrl.trim(), model: model.trim() },
      }
      if (apiKey.trim()) payload.video_api_key = apiKey.trim()
      const res = await api.settings.update(payload)
      const v = res?.video_api || {}
      setBaseUrl(v.base_url || baseUrl)
      setModel(v.model || model)
      setHasKey(Boolean(v.has_key))
      setApiKey('')
      setMsg({ ok: true, text: '视频生成配置已保存' })
    } catch (e) {
      setMsg({ ok: false, text: `保存失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">视频生成（P0.8）</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-dim)' }}>
        让 Agent 具备文生视频能力（generate_video 工具，异步任务，通常 1-5 分钟）。兼容 OpenAI 风格视频端点与火山方舟 Seedance：
        <code style={{ color: 'var(--accent)' }}> https://ark.cn-beijing.volces.com/api/v3</code>。
      </div>
      <div className="space-y-2.5">
        <div>
          <label className="text-xs" style={{ color: 'var(--text-dim)' }}>服务地址 Base URL</label>
          <input
            type="text"
            className="w-full mt-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
            placeholder="https://ark.cn-beijing.volces.com/api/v3"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs" style={{ color: 'var(--text-dim)' }}>模型名 Model</label>
          <input
            type="text"
            className="w-full mt-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
            placeholder="doubao-seedance-1-0-pro-250528"
            value={model}
            onChange={(e) => setModel(e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs" style={{ color: 'var(--text-dim)' }}>
            API Key {hasKey && <span style={{ color: 'var(--ok)' }}>（已配置，留空保持不变）</span>}
          </label>
          <input
            type="password"
            className="w-full mt-1 px-3 py-2 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
            placeholder={hasKey ? '••••••••（已保存）' : 'sk-...'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-3">
          <button
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--accent)', color: 'var(--accent-ink)', border: 'none', cursor: 'pointer' }}
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? '保存中…' : '保存配置'}
          </button>
          {msg && (
            <span className="text-xs" style={{ color: msg.ok ? 'var(--ok)' : 'var(--danger)' }}>
              {msg.text}
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

export default VideoGenManager
