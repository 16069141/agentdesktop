import React, { useEffect, useState } from 'react'
import { api } from '../../api'

/** P0.7 图像生成服务配置：OpenAI 兼容 images/generations 端点（火山方舟 Seedream / OpenRouter 等） */
const ImageGenManager: React.FC = () => {
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [hasKey, setHasKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    api.settings.get().then((s: any) => {
      const img = s?.image_api || {}
      setBaseUrl(img.base_url || '')
      setModel(img.model || '')
      setHasKey(Boolean(img.has_key))
    }).catch(() => {})
  }, [])

  const handleSave = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const payload: Record<string, unknown> = {
        image_api: { base_url: baseUrl.trim(), model: model.trim() },
      }
      if (apiKey.trim()) payload.image_api_key = apiKey.trim()
      const res = await api.settings.update(payload)
      const img = res?.image_api || {}
      setBaseUrl(img.base_url || baseUrl)
      setModel(img.model || model)
      setHasKey(Boolean(img.has_key))
      setApiKey('')
      setMsg({ ok: true, text: '图像生成配置已保存' })
    } catch (e) {
      setMsg({ ok: false, text: `保存失败：${e instanceof Error ? e.message : String(e)}` })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div className="font-medium mb-1">图像生成（P0.7）</div>
      <div className="text-xs mb-3" style={{ color: 'var(--text-dim)' }}>
        让 Agent 具备文生图能力（generate_image 工具）。兼容 OpenAI images 端点：火山方舟 Seedream 用
        <code style={{ color: 'var(--accent)' }}> https://ark.cn-beijing.volces.com/api/v3</code>，
        OpenRouter 用
        <code style={{ color: 'var(--accent)' }}> https://openrouter.ai/api/v1</code>
        ；也可填其他兼容服务地址。
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
            placeholder="doubao-seedream-4-0-250828"
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

export default ImageGenManager
