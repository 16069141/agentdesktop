import React, { useEffect, useState } from 'react'
import { api } from '../../api'
import ModelServersManager from './ModelServersManager'
import KnowledgeServersManager from './KnowledgeServersManager'
import EnterpriseManager from './EnterpriseManager'
import SearchServersManager from './SearchServersManager'
import McpServersManager from './McpServersManager'
import ImageGenManager from './ImageGenManager'

interface Settings {
  shell_whitelist: string[]
  dangerous_patterns: string[]
  max_shell_timeout_sec: number
  allowed_root_dirs: string[]
  context_ratio_system: number
  context_ratio_history: number
  context_ratio_generation: number
}

const SettingsView: React.FC = () => {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [keychainStatus, setKeychainStatus] = useState<{ available: boolean; backend?: string } | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [editing, setEditing] = useState<Record<string, string>>({})

  const loadSettings = async () => {
    try {
      setLoading(true)
      const [settingsData, keychainData] = await Promise.all([
        api.settings.get(),
        fetchKeychainStatus(),
      ])
      setSettings(settingsData)
      setKeychainStatus(keychainData)
    } catch (e) {
      setError(`加载设置失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }

  const fetchKeychainStatus = async () => {
    try {
      const token = await window.electronAPI?.getAgentToken() || ''
      const resp = await fetch(`http://127.0.0.1:8765/api/security/keychain/status`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (resp.ok) return await resp.json()
    } catch {}
    return { available: false }
  }

  useEffect(() => {
    loadSettings()
  }, [])

  const handleInputChange = (key: string, value: string) => {
    setEditing(prev => ({ ...prev, [key]: value }))
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      await api.settings.update({ ...editing })
      setSettings(prev => prev ? { ...prev, ...editing } : null)
      setSuccess('设置已保存')
      setEditing({})
    } catch (e) {
      setError(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = () => {
    setEditing({})
    setError(null)
  }

  if (loading) {
    return (
      <div className="p-6" style={{ color: 'var(--text)' }}>
        <div className="text-center py-8" style={{ color: 'var(--text-faint)' }}>加载中...</div>
      </div>
    )
  }

  return (
    <div className="p-6 overflow-y-auto h-full" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">设置</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        模型服务器连接 · 知识库连接 · 安全配置 · 上下文管理
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

      <div className="max-w-2xl space-y-4">
        {/* 模型服务器连接管理 */}
        <ModelServersManager />

        {/* 知识库连接管理 */}
        <KnowledgeServersManager />

        {/* 联网搜索服务（web_search 工具） */}
        <SearchServersManager />

        {/* 外部 MCP 服务（动态挂载工具） */}
        <McpServersManager />

        {/* P0.7 图像生成服务（generate_image 工具） */}
        <ImageGenManager />

        {/* 企业身份与权限（Phase B P0） */}
        <EnterpriseManager />

        {/* Shell 安全设置 */}
        <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <div className="font-medium mb-3">Shell 安全</div>
          <div className="space-y-3">
            <div>
              <label className="text-sm" style={{ color: 'var(--text-dim)' }}>命令白名单</label>
              <input
                type="text"
                value={editing.shell_whitelist ?? settings?.shell_whitelist.join(', ')}
                onChange={(e) => handleInputChange('shell_whitelist', e.target.value)}
                placeholder="ls, cat, pwd, grep, find"
                className="w-full mt-1 p-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
              />
              <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
                当前：{settings?.shell_whitelist.join(', ')}
              </div>
            </div>
            <div>
              <label className="text-sm" style={{ color: 'var(--text-dim)' }}>超时时间（秒）</label>
              <input
                type="number"
                value={editing.max_shell_timeout_sec ?? settings?.max_shell_timeout_sec}
                onChange={(e) => handleInputChange('max_shell_timeout_sec', e.target.value)}
                className="w-32 mt-1 p-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
              />
            </div>
            <div>
              <label className="text-sm" style={{ color: 'var(--text-dim)' }}>允许根目录</label>
              <input
                type="text"
                value={editing.allowed_root_dirs ?? settings?.allowed_root_dirs.join(', ')}
                onChange={(e) => handleInputChange('allowed_root_dirs', e.target.value)}
                placeholder="/home/user, /tmp"
                className="w-full mt-1 p-2 rounded-lg text-sm"
                style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
              />
            </div>
          </div>
        </div>

        {/* 上下文预算 */}
        <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <div className="font-medium mb-3">上下文预算分配</div>
          <div className="grid grid-cols-2 gap-4">
            {[
              { key: 'context_ratio_system', label: '系统提示', default: 0.15 },
              { key: 'context_ratio_history', label: '历史消息', default: 0.40 },
              { key: 'context_ratio_generation', label: '生成预留', default: 0.45 },
            ].map(({ key, label, default: def }) => (
              <div key={key}>
                <label className="text-sm" style={{ color: 'var(--text-dim)' }}>
                  {label} ({Math.round(def * 100)}%)
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={editing[key] ?? Math.round(((settings?.[key as keyof Settings] ?? def) as number) * 100)}
                  onChange={(e) => handleInputChange(key, e.target.value)}
                  className="w-full mt-1 p-2 rounded-lg text-sm"
                  style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
                />
              </div>
            ))}
          </div>
          <div className="text-xs mt-2" style={{ color: 'var(--text-faint)' }}>
            总和应为 100%
          </div>
        </div>

        {/* 密钥串状态 */}
        <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <div className="font-medium mb-3">密钥存储</div>
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm" style={{ color: 'var(--text-dim)' }}>Keychain 状态</span>
              <span className={`text-xs px-2 py-1 rounded ${keychainStatus?.available ? 'bg-green-500/20 text-green-500' : 'bg-yellow-500/20 text-yellow-500'}`}>
                {keychainStatus?.available ? `● 可用 (${keychainStatus.backend})` : '○ 不可用'}
              </span>
            </div>
            <div className="text-xs" style={{ color: 'var(--text-faint)' }}>
              macOS Keychain / Windows Credential Manager 自动选择
            </div>
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex gap-2">
          <button
            onClick={handleSave}
            disabled={saving || Object.keys(editing).length === 0}
            className="px-4 py-2 rounded-lg text-sm font-medium"
            style={{
              background: 'var(--primary)',
              color: 'white',
              opacity: saving || Object.keys(editing).length === 0 ? 0.5 : 1,
            }}
          >
            {saving ? '保存中...' : '保存设置'}
          </button>
          <button
            onClick={handleReset}
            disabled={!Object.keys(editing).length}
            className="px-4 py-2 rounded-lg text-sm"
            style={{
              background: 'var(--bg-elev)',
              border: '1px solid var(--border-soft)',
              opacity: !Object.keys(editing).length ? 0.5 : 1,
            }}
          >
            取消
          </button>
          <button
            onClick={loadSettings}
            className="px-4 py-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
          >
            刷新
          </button>
        </div>
      </div>
    </div>
  )
}

export default SettingsView
