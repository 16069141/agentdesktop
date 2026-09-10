import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface ConnectorType {
  type: string
  name: string
  description: string
  operations: Array<{ operation: string; method: string; path: string; risk: string; description: string }>
}

interface ConnectorItem {
  id: string
  type: string
  name: string
  baseUrl: string
  authType: string
  hasApiKey: boolean
  enabled: boolean
  timeoutSec: number
  lastHealthAt: number | null
  lastHealthOk: boolean | null
  operations?: Record<string, any> | null
}

interface DbConnItem {
  id: string
  name: string
  dbType: string
  dsn: string
  enabled: boolean
  maxRows: number
  timeoutSec: number
}

interface WebhookEvent {
  id: number
  hookId: string
  source: string
  payload: Record<string, any>
  processed: boolean
  createdAt: number
}

interface FieldMapping {
  id: string
  name: string
  connectorId: string
  sourceField: string
  targetField: string
  transform: { type: string }
  enabled: boolean
  createdAt: number
}

const TYPE_LABEL: Record<string, { label: string; desc: string }> = {
  erp: { label: 'ERP 企业资源计划', desc: '订单 · 库存 · 财务对账 · 采购 · 生产排程' },
  crm: { label: 'CRM 客户关系', desc: '客户 · 商机 · 跟进 · 合同回款 · 销售漏斗' },
  oa: { label: 'OA 办公自动化', desc: '审批流程 · 请假 · 报销 · 公告通知' },
}

const RISK_COLOR: Record<string, string> = { low: 'var(--ok)', medium: 'var(--warn)', high: 'var(--danger)' }

type Section = 'connector' | 'db' | 'webhook' | 'mapping'

const ConnectorsView: React.FC = () => {
  const [section, setSection] = useState<Section>('connector')

  // 企业连接器
  const [types, setTypes] = useState<ConnectorType[]>([])
  const [connectors, setConnectors] = useState<ConnectorItem[]>([])
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<ConnectorItem | null>(null)
  const [form, setForm] = useState({ id: '', type: 'erp', name: '', base_url: '', api_key: '', enabled: true })
  const [testResult, setTestResult] = useState<Record<string, { ok: boolean; detail?: string }>>({})

  // 数据库只读
  const [dbConns, setDbConns] = useState<DbConnItem[]>([])
  const [showDbForm, setShowDbForm] = useState(false)
  const [editingDbId, setEditingDbId] = useState<string | null>(null)
  const DEFAULT_DB_FORM = {
    id: '', name: '', db_type: 'sqlite', dsn: '',
    pg_host: '127.0.0.1', pg_port: '5432', pg_user: '', pg_password: '', pg_dbname: '',
    enabled: true, max_rows: 100, timeout_sec: 10,
  }
  const [dbForm, setDbForm] = useState(DEFAULT_DB_FORM)
  const [dbTest, setDbTest] = useState<Record<string, { ok: boolean; error?: string }>>({})
  const [dbQuery, setDbQuery] = useState<Record<string, { open: boolean; sql: string; res: any; loading: boolean }>>({})

  // Webhook
  const [events, setEvents] = useState<WebhookEvent[]>([])

  // 字段映射（P2）
  const [mappings, setMappings] = useState<FieldMapping[]>([])
  const [showMappingForm, setShowMappingForm] = useState(false)
  const [mappingForm, setMappingForm] = useState({
    id: '', name: '', connector_id: '', source_field: '', target_field: '',
    transform_type: 'none', enabled: true,
  })
  const [healthRunning, setHealthRunning] = useState(false)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadAll = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [t, c, d, m] = await Promise.all([
        api.connectors.types(),
        api.connectors.list(),
        api.dbConnectors.list(),
        api.ops.fieldMappings(),
      ])
      setTypes(Array.isArray(t) ? t : [])
      setConnectors(Array.isArray(c) ? c : [])
      setDbConns(Array.isArray(d) ? d : [])
      setMappings(Array.isArray(m) ? m : [])
    } catch (e) {
      setError(`加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [])
  useEffect(() => {
    if (section !== 'webhook') return
    api.webhooks.events()
      .then((ev) => setEvents(Array.isArray(ev) ? ev : []))
      .catch(() => setEvents([]))
  }, [section])

  // ===== 企业连接器 =====
  const startCreate = () => {
    setEditing(null)
    setForm({ id: '', type: 'erp', name: '', base_url: '', api_key: '', enabled: true })
    setShowForm(true)
  }
  const startEdit = (c: ConnectorItem) => {
    setEditing(c)
    setForm({ id: c.id, type: c.type, name: c.name, base_url: c.baseUrl, api_key: '', enabled: c.enabled })
    setShowForm(true)
  }
  const saveConnector = async () => {
    setError(null)
    try {
      if (editing) {
        await api.connectors.update(editing.id, {
          ...(form.name !== editing.name ? { name: form.name } : {}),
          ...(form.base_url !== editing.baseUrl ? { base_url: form.base_url } : {}),
          ...(form.enabled !== editing.enabled ? { enabled: form.enabled } : {}),
          ...(form.api_key ? { api_key: form.api_key } : {}),
        })
      } else {
        await api.connectors.create(form)
      }
      setShowForm(false)
      loadAll()
    } catch (e) {
      setError(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  const removeConnector = async (c: ConnectorItem) => {
    if (!window.confirm(`确认删除连接器「${c.name}」？关联密钥将一并删除。`)) return
    try {
      await api.connectors.remove(c.id)
      loadAll()
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  const testConnector = async (c: ConnectorItem) => {
    try {
      const res = await api.connectors.test(c.id)
      setTestResult((p) => ({ ...p, [c.id]: res }))
      loadAll()
    } catch (e) {
      setTestResult((p) => ({ ...p, [c.id]: { ok: false, detail: String(e) } }))
    }
  }
  const invokeOp = async (c: ConnectorItem, op: string) => {
    const paramsStr = window.prompt(`调用 ${op}（JSON 参数，可留空）`, '{}')
    if (paramsStr === null) return
    let params: Record<string, unknown> = {}
    try { params = paramsStr.trim() ? JSON.parse(paramsStr) : {} } catch {
      window.alert('参数不是合法 JSON')
      return
    }
    try {
      const res = await api.connectors.invoke(c.id, op, params)
      window.alert(`调用结果（HTTP ${res.status_code ?? '-'}）\n\n${JSON.stringify(res.data ?? res, null, 2).slice(0, 1500)}`)
    } catch (e) {
      window.alert(`调用失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  // ===== 数据库只读 =====
  /** 展示用脱敏：隐藏 DSN 中的密码 */
  const maskDsn = (dsn: string) =>
    dsn.replace(/\/\/([^:/@]+):([^@/]+)@/, '//$1:***@')

  const saveDb = async () => {
    setError(null)
    try {
      const form = { ...dbForm }
      if (form.db_type === 'postgres') {
        if (!form.pg_host.trim() || !form.pg_user.trim() || !form.pg_dbname.trim()) {
          setError('PostgreSQL 需填写 主机 / 用户 / 数据库名')
          return
        }
        form.dsn = `postgresql://${encodeURIComponent(form.pg_user)}:${encodeURIComponent(form.pg_password)}@${form.pg_host.trim()}:${Number(form.pg_port) || 5432}/${encodeURIComponent(form.pg_dbname.trim())}`
      }
      if (editingDbId) {
        // 编辑模式：仅更新可改字段，id 不可变
        await api.dbConnectors.update(editingDbId, {
          name: form.name,
          db_type: form.db_type,
          dsn: form.dsn,
          enabled: form.enabled,
          max_rows: form.max_rows,
          timeout_sec: form.timeout_sec,
        })
      } else {
        await api.dbConnectors.create(form)
      }
      setShowDbForm(false)
      setEditingDbId(null)
      loadAll()
    } catch (e) {
      setError(`保存失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  const editDb = (d: DbConnItem) => {
    setEditingDbId(d.id)
    const prefill: any = {
      ...DEFAULT_DB_FORM,
      id: d.id,
      name: d.name,
      db_type: d.dbType,
      dsn: d.dsn,
      enabled: d.enabled,
      max_rows: d.maxRows,
      timeout_sec: d.timeoutSec,
    }
    // 从 DSN 解析 PostgreSQL 各字段（密码回填，便于直接保存）
    if (d.dbType === 'postgres') {
      try {
        const u = new URL(d.dsn)
        prefill.pg_host = u.hostname
        prefill.pg_port = u.port || '5432'
        prefill.pg_user = decodeURIComponent(u.username)
        prefill.pg_password = decodeURIComponent(u.password)
        prefill.pg_dbname = decodeURIComponent(u.pathname.slice(1))
      } catch {
        // DSN 解析失败则留空，由用户手动填
      }
    }
    setDbForm(prefill)
    setShowDbForm(true)
  }
  const removeDb = async (d: DbConnItem) => {
    if (!window.confirm(`确认删除只读连接「${d.name}」？`)) return
    try {
      await api.dbConnectors.remove(d.id)
      loadAll()
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  const testDb = async (d: DbConnItem) => {
    try {
      const res = await api.dbConnectors.test(d.id)
      setDbTest((p) => ({ ...p, [d.id]: res }))
    } catch (e) {
      setDbTest((p) => ({ ...p, [d.id]: { ok: false, error: String(e) } }))
    }
  }
  const queryDb = async (d: DbConnItem) => {
    const defSql = d.dbType === 'postgres'
      ? 'SELECT tablename FROM pg_tables WHERE schemaname=current_schema() LIMIT 10'
      : 'SELECT * FROM sqlite_master LIMIT 10'
    setDbQuery((p) => ({
      ...p,
      [d.id]: p[d.id]
        ? { ...p[d.id], open: !p[d.id].open }
        : { open: true, sql: defSql, res: null, loading: false },
    }))
  }
  const runQuery = async (d: DbConnItem) => {
    const q = dbQuery[d.id]
    if (!q || !q.sql.trim()) return
    setDbQuery((p) => ({ ...p, [d.id]: { ...p[d.id], loading: true, res: null } }))
    try {
      const res = await api.dbConnectors.query(d.id, q.sql)
      setDbQuery((p) => ({ ...p, [d.id]: { ...p[d.id], loading: false, res } }))
    } catch (e) {
      setDbQuery((p) => ({
        ...p,
        [d.id]: { ...p[d.id], loading: false, res: { success: false, error: e instanceof Error ? e.message : String(e) } },
      }))
    }
  }

  const fmtTime = (ms: number | null | undefined) =>
    ms ? new Date(ms).toLocaleString('zh-CN', { hour12: false }) : '—'

  /** 手动健康巡检（P2：全量连通 + 断连写审计） */
  const runHealthCheck = async () => {
    setHealthRunning(true)
    setError(null)
    try {
      const r = await api.ops.healthCheck()
      window.alert(
        `健康巡检完成：${r.total} 项连接，正常 ${r.ok_count}，异常 ${r.down_count}` +
        (r.down_count
          ? `\n\n异常项：\n${r.down.map((d: any) => `• ${d.name}（${d.kind} ${d.id}）：${JSON.stringify(d.detail).slice(0, 120)}`).join('\n')}`
          : '')
      )
      loadAll()
    } catch (e) {
      setError(`健康巡检失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setHealthRunning(false)
    }
  }

  const saveMapping = async () => {
    setError(null)
    try {
      await api.ops.createMapping({
        id: mappingForm.id, name: mappingForm.name,
        connector_id: mappingForm.connector_id,
        source_field: mappingForm.source_field,
        target_field: mappingForm.target_field,
        transform: { type: mappingForm.transform_type },
        enabled: mappingForm.enabled,
      })
      setShowMappingForm(false)
      setMappingForm({ id: '', name: '', connector_id: '', source_field: '', target_field: '', transform_type: 'none', enabled: true })
      loadAll()
    } catch (e) {
      setError(`保存映射失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }
  const removeMapping = async (m: FieldMapping) => {
    if (!window.confirm(`确认删除字段映射「${m.name}」？`)) return
    try {
      await api.ops.removeMapping(m.id)
      loadAll()
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const segBtn = (id: Section, label: string) => (
    <button
      className="px-3 py-1.5 rounded-lg text-sm transition-colors"
      style={{
        background: section === id ? 'var(--accent-soft)' : 'transparent',
        color: section === id ? 'var(--accent)' : 'var(--text-dim)',
        border: 'none', cursor: 'pointer', fontWeight: section === id ? 600 : 400,
      }}
      onClick={() => setSection(id)}
    >
      {label}
    </button>
  )

  return (
    <div className="p-6 overflow-y-auto h-full" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">连接（Connections）</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        企业系统接入 · API 直连 / 数据库只读直连 / RPA / Webhook 四种方式
      </div>

      <div className="flex gap-1 mb-4 border-b pb-2" style={{ borderColor: 'var(--border-soft)' }}>
        {segBtn('connector', `企业系统（${connectors.length}）`)}
        {segBtn('db', `数据库只读（${dbConns.length}）`)}
        {segBtn('mapping', `字段映射（${mappings.length}）`)}
        {segBtn('webhook', `Webhook 事件（${events.length}）`)}
        <button
          className="ml-auto px-3 py-1.5 rounded-lg text-sm"
          style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
          disabled={healthRunning}
          onClick={runHealthCheck}
        >
          {healthRunning ? '巡检中...' : '🩺 健康巡检'}
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}

      {/* ============ 企业系统连接器 ============ */}
      {section === 'connector' && (
        <div>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm" style={{ color: 'var(--text-dim)' }}>
              ERP / CRM / OA 连接器：操作经端点模板映射到 REST 调用，密钥入系统钥匙串，操作级审计
            </div>
            <button
              className="px-3 py-1.5 rounded-lg text-sm"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
              onClick={startCreate}
            >
              + 新增连接器
            </button>
          </div>

          {showForm && (
            <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div className="font-medium text-sm mb-3">{editing ? `编辑 ${editing.name}` : '新增连接器'}</div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="ID（如 erp-kingdee）" value={form.id} disabled={!!editing}
                  onChange={(e) => setForm({ ...form, id: e.target.value })}
                />
                <select
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  value={form.type} disabled={!!editing}
                  onChange={(e) => setForm({ ...form, type: e.target.value })}
                >
                  {types.map((t) => <option key={t.type} value={t.type}>{TYPE_LABEL[t.type]?.label || t.name}</option>)}
                </select>
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="名称（如 金蝶云星空-生产库）" value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="Base URL（如 https://erp.example.com）" value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                />
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder={editing?.hasApiKey ? '已配置密钥，留空保持不变' : 'API Key（存入系统钥匙串）'} value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                />
                <label className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-dim)' }}>
                  <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
                  启用
                </label>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  className="px-3 py-1.5 rounded-lg text-sm"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                  onClick={saveConnector}
                >
                  保存
                </button>
                <button className="px-3 py-1.5 rounded-lg text-sm" style={{ color: 'var(--text-dim)' }} onClick={() => setShowForm(false)}>
                  取消
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <div style={{ color: 'var(--text-faint)' }}>加载中...</div>
          ) : connectors.length === 0 ? (
            <div className="rounded-xl p-8 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>尚未配置企业系统连接器</div>
              <div className="mt-1 text-xs" style={{ color: 'var(--text-faint)' }}>
                支持 API 直连（REST/OData），可对接金蝶 / 用友 / SAP / 销售易 / 泛微等；RPA 与 Webhook 通道见下方
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {connectors.map((c) => {
                const ops = types.find((t) => t.type === c.type)?.operations || []
                return (
                  <div key={c.id} className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm">{c.name}</span>
                      <span className="font-mono text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                        {c.type}
                      </span>
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: c.enabled ? 'var(--ok-soft)' : 'var(--bg-elev)', color: c.enabled ? 'var(--ok)' : 'var(--text-dim)' }}>
                        {c.enabled ? '已启用' : '已停用'}
                      </span>
                      {c.lastHealthOk !== null && (
                        <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: c.lastHealthOk ? 'var(--ok-soft)' : 'var(--error-soft)', color: c.lastHealthOk ? 'var(--ok)' : 'var(--error)' }}>
                          {c.lastHealthOk ? '健康' : '异常'}
                        </span>
                      )}
                      <span className="ml-auto flex gap-2 text-xs">
                        <button style={{ color: 'var(--accent)' }} onClick={() => testConnector(c)}>测试连接</button>
                        <button style={{ color: 'var(--text-dim)' }} onClick={() => startEdit(c)}>编辑</button>
                        <button style={{ color: 'var(--danger)' }} onClick={() => removeConnector(c)}>删除</button>
                      </span>
                    </div>
                    <div className="text-xs mb-2 font-mono" style={{ color: 'var(--text-faint)' }}>
                      {c.baseUrl} · {c.authType} · 超时 {c.timeoutSec}s · 最近检测 {fmtTime(c.lastHealthAt)}
                    </div>
                    {testResult[c.id] && (
                      <div className="text-xs mb-2" style={{ color: testResult[c.id].ok ? 'var(--ok)' : 'var(--error)' }}>
                        测试：{testResult[c.id].ok ? `通过（${testResult[c.id].detail ?? ''}）` : `失败（${testResult[c.id].detail ?? ''}）`}
                      </div>
                    )}
                    <div className="flex flex-wrap gap-1">
                      {ops.map((op) => (
                        <button
                          key={op.operation}
                          className="text-xs px-2 py-0.5 rounded font-mono"
                          style={{ background: 'var(--code-bg)', color: RISK_COLOR[op.risk] || 'var(--text-dim)' }}
                          title={`${op.method} ${op.path} · ${op.description}`}
                          onClick={() => invokeOp(c, op.operation)}
                        >
                          {op.operation}
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* ============ 数据库只读 ============ */}
      {section === 'db' && (
        <div>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm" style={{ color: 'var(--text-dim)' }}>
              SQLite 文件级只读（mode=ro）· PostgreSQL 只读事务 · 语句级只读校验（SELECT/WITH/EXPLAIN）· 写操作一律拒绝
            </div>
            <button
              className="px-3 py-1.5 rounded-lg text-sm"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
              onClick={() => { setEditingDbId(null); setDbForm(DEFAULT_DB_FORM); setShowDbForm(true) }}
            >
              + 新增只读连接
            </button>
          </div>

          {showDbForm && (
            <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div className="font-medium text-sm mb-3 flex items-center gap-2">
                {editingDbId ? '编辑数据库只读连接' : '新增数据库只读连接'}
                <select
                  className="text-xs rounded-lg px-2 py-1 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  value={dbForm.db_type}
                  onChange={(e) => setDbForm({ ...dbForm, db_type: e.target.value })}
                >
                  <option value="sqlite">SQLite</option>
                  <option value="postgres">PostgreSQL</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)', opacity: editingDbId ? 0.5 : 1 }}
                  placeholder="ID（如 db-prod-ro）" value={dbForm.id} disabled={!!editingDbId}
                  onChange={(e) => setDbForm({ ...dbForm, id: e.target.value })}
                />
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="名称（如 生产库-只读）" value={dbForm.name}
                  onChange={(e) => setDbForm({ ...dbForm, name: e.target.value })}
                />
                {dbForm.db_type === 'postgres' ? (
                  <>
                    <input
                      className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                      style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      placeholder="主机（如 127.0.0.1）" value={dbForm.pg_host}
                      onChange={(e) => setDbForm({ ...dbForm, pg_host: e.target.value })}
                    />
                    <input
                      className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                      style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      placeholder="端口（默认 5432）" value={dbForm.pg_port}
                      onChange={(e) => setDbForm({ ...dbForm, pg_port: e.target.value })}
                    />
                    <input
                      className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                      style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      placeholder="用户名（如 postgres）" value={dbForm.pg_user}
                      onChange={(e) => setDbForm({ ...dbForm, pg_user: e.target.value })}
                    />
                    <input
                      type="password"
                      className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                      style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      placeholder="密码" value={dbForm.pg_password}
                      onChange={(e) => setDbForm({ ...dbForm, pg_password: e.target.value })}
                    />
                    <input
                      className="col-span-2 text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono"
                      style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                      placeholder="数据库名（如 price_library）" value={dbForm.pg_dbname}
                      onChange={(e) => setDbForm({ ...dbForm, pg_dbname: e.target.value })}
                    />
                  </>
                ) : (
                  <input
                    className="col-span-2 text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono"
                    style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                    placeholder="SQLite 文件绝对路径（如 /opt/data/prod.db）" value={dbForm.dsn}
                    onChange={(e) => setDbForm({ ...dbForm, dsn: e.target.value })}
                  />
                )}
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="最大返回行数（默认 100）" type="number" value={dbForm.max_rows}
                  onChange={(e) => setDbForm({ ...dbForm, max_rows: Number(e.target.value) })}
                />
                <input
                  className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
                  style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="超时秒数（默认 10）" type="number" value={dbForm.timeout_sec}
                  onChange={(e) => setDbForm({ ...dbForm, timeout_sec: Number(e.target.value) })}
                />
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  className="px-3 py-1.5 rounded-lg text-sm"
                  style={{ background: 'var(--accent)', color: '#fff' }}
                  onClick={saveDb}
                >
                  保存
                </button>
                <button className="px-3 py-1.5 rounded-lg text-sm" style={{ color: 'var(--text-dim)' }} onClick={() => { setShowDbForm(false); setEditingDbId(null) }}>
                  取消
                </button>
              </div>
            </div>
          )}

          {dbConns.length === 0 ? (
            <div className="rounded-xl p-8 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>尚未配置数据库只读连接</div>
              <div className="mt-1 text-xs" style={{ color: 'var(--text-faint)' }}>
                已实现 SQLite / PostgreSQL 只读直连；MySQL 适配器预留，后续版本接入
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {dbConns.map((d) => (
                <div key={d.id} className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-medium text-sm">{d.name}</span>
                    <span className="font-mono text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                      {d.dbType}
                    </span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: d.enabled ? 'var(--ok-soft)' : 'var(--bg-elev)', color: d.enabled ? 'var(--ok)' : 'var(--text-dim)' }}>
                      {d.enabled ? '已启用' : '已停用'}
                    </span>
                    {dbTest[d.id] && (
                      <span className="text-xs" style={{ color: dbTest[d.id].ok ? 'var(--ok)' : 'var(--error)' }}>
                        {dbTest[d.id].ok ? '连接正常' : `失败：${dbTest[d.id].error}`}
                      </span>
                    )}
                    <span className="ml-auto flex gap-2 text-xs">
                      <button style={{ color: 'var(--accent)' }} onClick={() => editDb(d)}>编辑</button>
                      <button style={{ color: 'var(--accent)' }} onClick={() => testDb(d)}>测试连接</button>
                      <button style={{ color: 'var(--accent)' }} onClick={() => queryDb(d)}>查询</button>
                      <button style={{ color: 'var(--danger)' }} onClick={() => removeDb(d)}>删除</button>
                    </span>
                  </div>
                  <div className="text-xs font-mono" style={{ color: 'var(--text-faint)' }}>
                    {maskDsn(d.dsn)} · 最多 {d.maxRows} 行 · 超时 {d.timeoutSec}s
                  </div>
                  {dbQuery[d.id]?.open && (
                    <div className="mt-3 pt-3" style={{ borderTop: '1px dashed var(--border)' }}>
                      <div className="flex gap-2 mb-2">
                        <input
                          className="flex-1 text-xs rounded-lg px-2.5 py-1.5 outline-none font-mono"
                          style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                          placeholder="只读 SQL（仅 SELECT / WITH / EXPLAIN）" value={dbQuery[d.id].sql}
                          onChange={(e) => setDbQuery((p) => ({ ...p, [d.id]: { ...p[d.id], sql: e.target.value } }))}
                          onKeyDown={(e) => e.key === 'Enter' && runQuery(d)}
                        />
                        <button
                          className="px-3 py-1.5 rounded-lg text-xs"
                          style={{ background: 'var(--accent)', color: '#fff', opacity: dbQuery[d.id].loading ? 0.6 : 1 }}
                          onClick={() => runQuery(d)}
                        >
                          {dbQuery[d.id].loading ? '执行中…' : '执行'}
                        </button>
                      </div>
                      {dbQuery[d.id].res && (
                        dbQuery[d.id].res.success ? (
                          <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border-soft)' }}>
                            <div className="text-xs px-2.5 py-1.5" style={{ background: 'var(--code-bg)', color: 'var(--text-dim)' }}>
                              返回 {dbQuery[d.id].res.count} 行{dbQuery[d.id].res.truncated ? '（已截断）' : ''} · 耗时 {dbQuery[d.id].res.latency_ms}ms
                            </div>
                            <div className="overflow-auto max-h-56">
                              <table className="text-xs w-full">
                                <thead>
                                  <tr style={{ background: 'var(--bg-elev)' }}>
                                    {(dbQuery[d.id].res.columns || []).map((c: string, i: number) => (
                                      <th key={i} className="text-left px-2.5 py-1.5 font-medium whitespace-nowrap" style={{ color: 'var(--text)' }}>{c}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {(dbQuery[d.id].res.rows || []).map((r: any, ri: number) => (
                                    <tr key={ri} style={{ borderTop: '1px solid var(--border-soft)' }}>
                                      {(dbQuery[d.id].res.columns || []).map((c: string, ci: number) => (
                                        <td key={ci} className="px-2.5 py-1.5 whitespace-nowrap" style={{ color: 'var(--text-dim)' }}>
                                          {r[c] === null || r[c] === undefined ? 'NULL' : String(r[c])}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        ) : (
                          <div className="text-xs rounded-lg px-2.5 py-2" style={{ background: 'var(--error-soft, rgba(255,80,80,.1))', color: 'var(--error)' }}>
                            查询失败：{dbQuery[d.id].res.error}
                          </div>
                        )
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ============ 字段映射 ============ */}
      {section === 'mapping' && (
        <div>
          <div className="mb-3 flex items-center justify-between">
            <div className="text-sm" style={{ color: 'var(--text-dim)' }}>
              跨系统字段映射（源字段 → 目标字段 + 变换类型），JSON 配置编辑；可视化拖拽在 P3 落实
            </div>
            <button
              className="px-3 py-1.5 rounded-lg text-sm"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
              onClick={() => setShowMappingForm(true)}
            >
              + 新增映射
            </button>
          </div>

          {showMappingForm && (
            <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div className="font-medium text-sm mb-3">新增字段映射</div>
              <div className="grid grid-cols-2 gap-2">
                <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="ID（如 map_order_customer）" value={mappingForm.id}
                  onChange={(e) => setMappingForm({ ...mappingForm, id: e.target.value })} />
                <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="名称（如 订单客户映射）" value={mappingForm.name}
                  onChange={(e) => setMappingForm({ ...mappingForm, name: e.target.value })} />
                <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="连接器 ID（如 conn_erp_prod）" value={mappingForm.connector_id}
                  onChange={(e) => setMappingForm({ ...mappingForm, connector_id: e.target.value })} />
                <select className="text-sm rounded-lg px-2.5 py-1.5 outline-none" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  value={mappingForm.transform_type}
                  onChange={(e) => setMappingForm({ ...mappingForm, transform_type: e.target.value })}>
                  <option value="none">直接映射</option>
                  <option value="map">枚举映射</option>
                  <option value="format">格式化</option>
                  <option value="join">拼接</option>
                </select>
                <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="源字段（如 order.customer_id）" value={mappingForm.source_field}
                  onChange={(e) => setMappingForm({ ...mappingForm, source_field: e.target.value })} />
                <input className="text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono" style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
                  placeholder="目标字段（如 crm.customer_code）" value={mappingForm.target_field}
                  onChange={(e) => setMappingForm({ ...mappingForm, target_field: e.target.value })} />
              </div>
              <div className="mt-3 flex gap-2">
                <button className="px-3 py-1.5 rounded-lg text-sm" style={{ background: 'var(--accent)', color: '#fff' }} onClick={saveMapping}>保存</button>
                <button className="px-3 py-1.5 rounded-lg text-sm" style={{ color: 'var(--text-dim)' }} onClick={() => setShowMappingForm(false)}>取消</button>
              </div>
            </div>
          )}

          {mappings.length === 0 ? (
            <div className="rounded-xl p-8 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>尚未配置字段映射</div>
            </div>
          ) : (
            <div className="space-y-2">
              {mappings.map((m) => (
                <div key={m.id} className="rounded-xl p-4 flex items-center gap-3" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-medium text-sm">{m.name}</span>
                      <span className="font-mono text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                        {m.connectorId}
                      </span>
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: m.enabled ? 'var(--ok-soft)' : 'var(--bg-elev)', color: m.enabled ? 'var(--ok)' : 'var(--text-dim)' }}>
                        {m.enabled ? '已启用' : '已停用'}
                      </span>
                    </div>
                    <div className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
                      {m.sourceField} → {m.targetField} · {m.transform.type}
                    </div>
                  </div>
                  <button className="text-xs shrink-0" style={{ color: 'var(--danger)' }} onClick={() => removeMapping(m)}>删除</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ============ Webhook 事件 ============ */}
      {section === 'webhook' && (
        <div>
          <div className="mb-3 text-sm" style={{ color: 'var(--text-dim)' }}>
            外部系统事件接收端点 <span className="font-mono" style={{ color: 'var(--accent)' }}>POST /api/webhooks/&lt;hook_id&gt;</span>，事件落库供 P3 工作流触发器消费
          </div>
          {events.length === 0 ? (
            <div className="rounded-xl p-8 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>暂无接收事件</div>
            </div>
          ) : (
            <div className="space-y-2">
              {events.map((ev) => (
                <div key={ev.id} className="rounded-xl p-3 flex items-start gap-3" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                  <span className="font-mono text-xs px-1.5 py-0.5 rounded shrink-0" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                    {ev.hookId}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs font-mono truncate" style={{ color: 'var(--text-dim)' }}>
                      {JSON.stringify(ev.payload)}
                    </div>
                    <div className="text-xs mt-0.5" style={{ color: 'var(--text-faint)' }}>
                      #{ev.id} · {fmtTime(ev.createdAt)} · {ev.processed ? '已处理' : '待处理'}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default ConnectorsView
