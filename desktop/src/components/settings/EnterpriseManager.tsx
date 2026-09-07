import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface EnterpriseUser {
  id: string
  username: string
  displayName: string
  role: string
  dataScope: string
  department: string
  ssoProvider: string | null
  status: boolean
}

interface SSOProvider {
  name: string
  protocol: string
  hasSecret?: boolean
}

const SCOPE_DESC: Record<string, string> = {
  personal: '个人数据',
  department: '部门数据',
  global: '全局数据',
}

const ROLE_DESC: Record<string, string> = {
  admin: '管理员',
  manager: '主管',
  member: '成员',
}

const EnterpriseManager: React.FC = () => {
  const [users, setUsers] = useState<EnterpriseUser[]>([])
  const [providers, setProviders] = useState<Record<string, SSOProvider>>({})
  const [form, setForm] = useState({ username: '', display_name: '', role: 'member', data_scope: 'personal', department: '' })
  const [ssoForm, setSsoForm] = useState({ name: '', protocol: 'oidc', issuer: '', client_id: '', client_secret: '', redirect_uri: '' })
  const [msg, setMsg] = useState<{ ok?: string; err?: string }>({})

  const load = useCallback(async () => {
    try {
      const [u, p] = await Promise.all([api.enterprise.users(), api.enterprise.ssoProviders()])
      setUsers(Array.isArray(u) ? u : [])
      setProviders(typeof p === 'object' && p ? p : {})
    } catch (e) {
      setMsg({ err: `加载失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  useEffect(() => { load() }, [])

  const addUser = async () => {
    if (!form.username.trim()) return
    setMsg({})
    try {
      await api.enterprise.upsertUser(form)
      setForm({ username: '', display_name: '', role: 'member', data_scope: 'personal', department: '' })
      load()
    } catch (e) {
      setMsg({ err: `新增失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const deleteUser = async (id: string) => {
    if (!window.confirm('确认删除该企业账号？')) return
    await api.enterprise.deleteUser(id)
    load()
  }

  const saveSso = async () => {
    if (!ssoForm.name.trim()) return
    setMsg({})
    try {
      await api.enterprise.saveSso(ssoForm)
      setSsoForm({ name: '', protocol: 'oidc', issuer: '', client_id: '', client_secret: '', redirect_uri: '' })
      load()
    } catch (e) {
      setMsg({ err: `SSO 配置失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const deleteSso = async (name: string) => {
    await api.enterprise.deleteSso(name)
    load()
  }

  const inputStyle = {
    background: 'var(--surf-input)',
    color: 'var(--text)',
    border: '1px solid var(--border)',
  } as React.CSSProperties

  return (
    <div className="p-4 rounded-xl space-y-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
      <div>
        <div className="font-medium mb-1">企业身份与权限（统一认证）</div>
        <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
          企业账号 · 数据范围（个人/部门/全局）· SSO（OIDC 已实现，CAS/LDAP 预留）
        </div>
        {msg.err && <div className="mb-2 text-xs" style={{ color: 'var(--danger)' }}>{msg.err}</div>}

        {/* 用户列表 */}
        <div className="space-y-1.5 mb-3">
          {users.map((u) => (
            <div key={u.id} className="flex items-center gap-2 text-xs p-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <span style={{ color: 'var(--text)' }}>{u.displayName || u.username}</span>
              <span className="px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>
                {ROLE_DESC[u.role] || u.role}
              </span>
              <span className="px-1.5 py-0.5 rounded" style={{ background: 'var(--bg)', color: 'var(--text-dim)' }}>
                {SCOPE_DESC[u.dataScope] || u.dataScope}
              </span>
              {u.department && <span style={{ color: 'var(--text-faint)' }}>{u.department}</span>}
              <span className="ml-auto flex items-center gap-2">
                {u.ssoProvider && <span className="font-mono" style={{ color: 'var(--text-faint)' }}>{u.ssoProvider}</span>}
                <button style={{ color: 'var(--danger)' }} onClick={() => deleteUser(u.id)}>删除</button>
              </span>
            </div>
          ))}
          {users.length === 0 && <div className="text-xs" style={{ color: 'var(--text-faint)' }}>暂无企业账号，下方新增</div>}
        </div>

        {/* 新增用户 */}
        <div className="flex gap-2 flex-wrap">
          <input className="text-sm rounded-lg px-2 px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="用户名 *" value={form.username}
            onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="显示名" value={form.display_name}
            onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          <select className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="member">成员</option>
            <option value="manager">主管</option>
            <option value="admin">管理员</option>
          </select>
          <select className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} value={form.data_scope}
            onChange={(e) => setForm({ ...form, data_scope: e.target.value })}>
            <option value="personal">个人数据</option>
            <option value="department">部门数据</option>
            <option value="global">全局数据</option>
          </select>
          <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="部门" value={form.department}
            onChange={(e) => setForm({ ...form, department: e.target.value })} />
          <button className="px-3 py-1.5 rounded-lg text-sm" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }} onClick={addUser}>
            新增
          </button>
        </div>
      </div>

      {/* SSO 配置 */}
      <div className="border-t pt-3" style={{ borderColor: 'var(--border-soft)' }}>
        <div className="font-medium mb-2 text-sm">SSO 登录配置</div>
        <div className="space-y-1.5 mb-2">
          {Object.values(providers).map((p) => (
            <div key={p.name} className="flex items-center gap-2 text-xs p-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <span style={{ color: 'var(--text)' }}>{p.name}</span>
              <span className="font-mono px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                {p.protocol}
              </span>
              <span style={{ color: p.hasSecret ? 'var(--ok)' : 'var(--warn)' }}>
                {p.hasSecret ? '密钥已存' : '未配置密钥'}
              </span>
              <button className="ml-auto" style={{ color: 'var(--danger)' }} onClick={() => deleteSso(p.name)}>移除</button>
            </div>
          ))}
          {Object.keys(providers).length === 0 && (
            <div className="text-xs" style={{ color: 'var(--text-faint)' }}>未配置 SSO；支持 OIDC/OAuth2，CAS/LDAP 预留</div>
          )}
        </div>
        <div className="flex gap-2 flex-wrap">
          <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="名称（如 corp-oidc）" value={ssoForm.name}
            onChange={(e) => setSsoForm({ ...ssoForm, name: e.target.value })} />
          <select className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} value={ssoForm.protocol}
            onChange={(e) => setSsoForm({ ...ssoForm, protocol: e.target.value })}>
            <option value="oidc">OIDC/OAuth2</option>
            <option value="cas">CAS（预留）</option>
            <option value="ldap">LDAP（预留）</option>
          </select>
          <input className="flex-1 min-w-40 text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="Issuer（https://sso.example.com/...）" value={ssoForm.issuer}
            onChange={(e) => setSsoForm({ ...ssoForm, issuer: e.target.value })} />
          <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="Client ID" value={ssoForm.client_id}
            onChange={(e) => setSsoForm({ ...ssoForm, client_id: e.target.value })} />
          <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={inputStyle} placeholder="Client Secret" type="password" value={ssoForm.client_secret}
            onChange={(e) => setSsoForm({ ...ssoForm, client_secret: e.target.value })} />
          <button className="px-3 py-1.5 rounded-lg text-sm" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }} onClick={saveSso}>
            保存
          </button>
        </div>
      </div>
    </div>
  )
}

export default EnterpriseManager
