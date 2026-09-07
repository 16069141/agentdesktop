import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface Workflow {
  id: string
  name: string
  description: string
  triggerType: string
  triggerConfig: Record<string, any>
  steps: any[]
  enabled: boolean
  createdBy: string
  createdAt: number
  updatedAt: number
  lastRun?: WorkflowRun | null
  recentRuns?: WorkflowRun[]
}

interface WorkflowRun {
  id: string
  workflowId: string
  trigger: string
  status: string
  error: string
  logs: any[]
  startedAt: number
  finishedAt: number | null
}

const TRIGGER_LABEL: Record<string, string> = {
  manual: '手动', schedule: '定时', event: '事件',
  webhook: 'Webhook', db: '数据库',
}
const STATUS_LABEL: Record<string, string> = {
  success: '成功', failed: '失败', skipped: '跳过', running: '运行中',
}

const WorkflowsView: React.FC = () => {
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [templates, setTemplates] = useState<any[]>([])
  const [detail, setDetail] = useState<Workflow | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [form, setForm] = useState({
    id: '', name: '', description: '', trigger_type: 'webhook',
    trigger_config: '{}', steps: '', template: '',
  })
  const [runPayload, setRunPayload] = useState('{}')
  const [running, setRunning] = useState(false)
  const [diagnosis, setDiagnosis] = useState<{ [runId: string]: any }>({})
  const [diagLoading, setDiagLoading] = useState<string | null>(null)

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [w, t] = await Promise.all([api.workflows.list(), api.workflows.templates()])
      setWorkflows(Array.isArray(w) ? w : [])
      setTemplates(Array.isArray(t) ? t : [])
    } catch (e) {
      setError(`加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadList() }, [])

  const loadDetail = async (id: string) => {
    try {
      const p = await api.workflows.get(id)
      setDetail(p)
    } catch (e) {
      setError(`加载失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const applyTemplate = (tplId: string) => {
    const t = templates.find((x) => x.id === tplId)
    if (!t) return
    setForm((f) => ({
      ...f,
      id: `${t.id.replace('template_', 'wf_')}_${Date.now().toString(36).slice(-4)}`,
      name: t.name,
      description: t.description,
      trigger_type: t.triggerType,
      trigger_config: JSON.stringify(t.triggerConfig, null, 2),
      steps: JSON.stringify(t.steps, null, 2),
      template: tplId,
    }))
  }

  const create = async () => {
    setError(null)
    let trigger_config: any = {}
    let steps: any[] = []
    try {
      trigger_config = JSON.parse(form.trigger_config || '{}')
      steps = JSON.parse(form.steps || '[]')
    } catch (e) {
      setError(`JSON 解析失败：${e instanceof Error ? e.message : String(e)}`)
      return
    }
    if (!form.name.trim()) { setError('请输入工作流名称'); return }
    if (!Array.isArray(steps) || steps.length === 0) { setError('steps 必须是非空 JSON 数组'); return }
    try {
      await api.workflows.create({
        id: form.id || undefined, name: form.name, description: form.description,
        trigger_type: form.trigger_type, trigger_config, steps, enabled: true,
      })
      setShowCreate(false)
      setForm({ id: '', name: '', description: '', trigger_type: 'webhook', trigger_config: '{}', steps: '', template: '' })
      loadList()
    } catch (e) {
      setError(`创建失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const toggleEnabled = async (wf: Workflow) => {
    try {
      await api.workflows.update(wf.id, { enabled: !wf.enabled })
      if (detail?.id === wf.id) loadDetail(wf.id)
      loadList()
    } catch (e) {
      setError(`更新失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const remove = async (wf: Workflow) => {
    if (!window.confirm(`确认删除工作流「${wf.name}」？执行记录将一并删除。`)) return
    try {
      await api.workflows.remove(wf.id)
      if (detail?.id === wf.id) setDetail(null)
      loadList()
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const runNow = async () => {
    if (!detail) return
    setRunning(true)
    setError(null)
    let payload: Record<string, any> = {}
    try {
      payload = JSON.parse(runPayload || '{}')
    } catch (e) {
      setError(`payload JSON 解析失败：${e instanceof Error ? e.message : String(e)}`)
      setRunning(false)
      return
    }
    try {
      const r = await api.workflows.run(detail.id, payload)
      setError(`运行完成：${STATUS_LABEL[r.status] || r.status}${r.error ? `（${r.error}）` : ''}`)
      loadDetail(detail.id)
      loadList()
    } catch (e) {
      setError(`运行失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setRunning(false)
    }
  }

  /** P4：失败原因自动诊断 + 修复建议 */
  const diagnose = async (runId: string) => {
    if (diagnosis[runId]) return
    setDiagLoading(runId)
    setError(null)
    try {
      const d = await api.ops.diagnostics(runId)
      setDiagnosis((prev) => ({ ...prev, [runId]: d }))
    } catch (e) {
      setError(`诊断失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setDiagLoading(null)
    }
  }

  const fmt = (ms: number | null | undefined) =>
    ms ? new Date(ms).toLocaleString('zh-CN', { hour12: false }) : '—'

  const inputStyle: React.CSSProperties = {
    background: 'var(--surf-input)', color: 'var(--text)',
    border: '1px solid var(--border)',
  }

  return (
    <div className="p-6 overflow-y-auto h-full" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">自动化编排</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        工作流引擎 · 触发器（定时/Webhook/数据库阈值/手动）· 跨系统编排（CRM→ERP→WMS→通知）
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}

      <div className="flex gap-6">
        {/* 左：列表 + 新建 */}
        <div className="w-80 shrink-0">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">工作流（{workflows.length}）</span>
            <button
              className="px-2.5 py-1 rounded-lg text-sm"
              style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
              onClick={() => setShowCreate((v) => !v)}
            >
              + 新建
            </button>
          </div>

          {showCreate && (
            <div className="p-3 rounded-xl mb-3 space-y-2" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
              <div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-dim)' }}>从模板开始</div>
                <select
                  className="w-full text-sm rounded-lg px-2 py-1.5 outline-none"
                  style={inputStyle} value={form.template}
                  onChange={(e) => applyTemplate(e.target.value)}
                >
                  <option value="">— 选择模板 —</option>
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>{t.name}</option>
                  ))}
                </select>
              </div>
              <input
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={inputStyle} placeholder="名称" value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
              <input
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={inputStyle} placeholder="ID（留空自动生成）" value={form.id}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
              />
              <input
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={inputStyle} placeholder="描述" value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
              />
              <select
                className="w-full text-sm rounded-lg px-2 py-1.5 outline-none"
                style={inputStyle} value={form.trigger_type}
                onChange={(e) => setForm({ ...form, trigger_type: e.target.value })}
              >
                <option value="manual">手动</option>
                <option value="webhook">Webhook</option>
                <option value="schedule">定时（cron）</option>
                <option value="db">数据库阈值</option>
                <option value="event">事件</option>
              </select>
              <textarea
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono"
                style={{ ...inputStyle, minHeight: 56 }} placeholder='触发器配置 JSON，如 {"hook_id":"crm-events","event":"deal.won"}'
                value={form.trigger_config}
                onChange={(e) => setForm({ ...form, trigger_config: e.target.value })}
              />
              <textarea
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono"
                style={{ ...inputStyle, minHeight: 110 }} placeholder='步骤 DSL JSON 数组，如 [{"id":"n","type":"notify","config":{"message":"hi","channel":"audit"}}]'
                value={form.steps}
                onChange={(e) => setForm({ ...form, steps: e.target.value })}
              />
              <button
                className="w-full px-3 py-1.5 rounded-lg text-sm"
                style={{ background: 'var(--accent)', color: '#fff' }}
                onClick={create}
              >
                创建
              </button>
            </div>
          )}

          {loading ? (
            <div style={{ color: 'var(--text-faint)' }}>加载中...</div>
          ) : workflows.length === 0 ? (
            <div className="rounded-xl p-6 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>暂无工作流</div>
            </div>
          ) : (
            <div className="space-y-2">
              {workflows.map((wf) => (
                <div
                  key={wf.id}
                  className="rounded-xl p-3 cursor-pointer"
                  style={{
                    background: detail?.id === wf.id ? 'var(--accent-soft)' : 'var(--bg-panel)',
                    border: `1px solid ${detail?.id === wf.id ? 'var(--accent)' : 'var(--border-soft)'}`,
                  }}
                  onClick={() => loadDetail(wf.id)}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium flex-1 truncate">{wf.name}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded shrink-0" style={{ background: 'var(--bg-elev)', color: 'var(--text-faint)' }}>
                      {TRIGGER_LABEL[wf.triggerType] || wf.triggerType}
                    </span>
                    <button
                      className={`text-xs px-1.5 py-0.5 rounded shrink-0 ${wf.enabled ? '' : ''}`}
                      style={{
                        background: wf.enabled ? 'var(--ok-soft)' : 'var(--bg-elev)',
                        color: wf.enabled ? 'var(--ok)' : 'var(--text-dim)',
                      }}
                      onClick={(e) => { e.stopPropagation(); toggleEnabled(wf) }}
                      title={wf.enabled ? '点击停用' : '点击启用'}
                    >
                      {wf.enabled ? '启用' : '停用'}
                    </button>
                  </div>
                  <div className="flex items-center gap-2 mt-1">
                    {wf.lastRun && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--text-faint)' }}>
                        {STATUS_LABEL[wf.lastRun.status]} · {fmt(wf.lastRun.startedAt)}
                      </span>
                    )}
                    <button
                      className="text-xs ml-auto" style={{ color: 'var(--danger)' }}
                      onClick={(e) => { e.stopPropagation(); remove(wf) }}
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右：详情 */}
        <div className="flex-1 min-w-0">
          {!detail ? (
            <div className="rounded-xl p-10 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>选择左侧工作流查看详情 / 手动触发</div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-base font-semibold">{detail.name}</span>
                  <span className="text-xs px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                    {detail.id}
                  </span>
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}>
                    {TRIGGER_LABEL[detail.triggerType] || detail.triggerType} 触发器
                  </span>
                  <span className="ml-auto text-xs" style={{ color: 'var(--text-faint)' }}>
                    创建 {fmt(detail.createdAt)} · {detail.createdBy}
                  </span>
                </div>
                <div className="text-sm mb-3" style={{ color: 'var(--text-dim)' }}>{detail.description || '（无描述）'}</div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <div className="text-xs mb-1 font-medium" style={{ color: 'var(--text-dim)' }}>触发器配置</div>
                    <pre className="text-xs p-2 rounded-lg overflow-auto" style={{ background: 'var(--code-bg)', color: 'var(--text-dim)', maxHeight: 120 }}>
                      {JSON.stringify(detail.triggerConfig, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <div className="text-xs mb-1 font-medium" style={{ color: 'var(--text-dim)' }}>步骤（{detail.steps.length}）</div>
                    <div className="space-y-1 max-h-32 overflow-auto pr-1">
                      {detail.steps.map((s, i) => (
                        <div key={i} className="text-xs rounded px-2 py-1 flex items-center gap-2" style={{ background: 'var(--bg-elev)' }}>
                          <span className="font-mono" style={{ color: 'var(--accent)' }}>{s.id}</span>
                          <span className="px-1 rounded" style={{ background: 'var(--code-bg)', color: 'var(--text-faint)' }}>{s.type}</span>
                          <span className="truncate flex-1" style={{ color: 'var(--text-dim)' }}>
                            {s.type === 'notify' ? s.config?.message : s.type === 'connector' ? `${s.config?.type || s.config?.connector_id} · ${s.config?.operation}` : s.type === 'data' ? s.config?.sql : s.type === 'condition' ? `${s.config?.field} ${s.config?.op} ${s.config?.value}` : JSON.stringify(s.config).slice(0, 60)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* 手动触发 */}
                <div className="flex gap-2 mt-3">
                  <input
                    className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none font-mono"
                    style={inputStyle} placeholder='payload JSON（如 {"customer":"Acme","amount":100}）'
                    value={runPayload}
                    onChange={(e) => setRunPayload(e.target.value)}
                  />
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm shrink-0"
                    style={{ background: 'var(--accent)', color: '#fff' }}
                    disabled={running}
                    onClick={runNow}
                  >
                    {running ? '运行中...' : '▶ 手动触发'}
                  </button>
                </div>
              </div>

              {/* 执行记录 */}
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="text-sm font-medium mb-2">执行记录（{(detail.recentRuns || []).length}）</div>
                {(detail.recentRuns || []).length === 0 ? (
                  <div className="text-xs" style={{ color: 'var(--text-faint)' }}>暂无执行记录</div>
                ) : (
                  <div className="space-y-2">
                    {(detail.recentRuns || []).map((r) => (
                      <div key={r.id} className="rounded-lg p-3" style={{ background: 'var(--bg-elev)' }}>
                        <div className="flex items-center gap-2 text-xs mb-1">
                          <span className="font-mono" style={{ color: 'var(--accent)' }}>{r.id}</span>
                          <span className="px-1.5 py-0.5 rounded"
                            style={{
                              background: r.status === 'success' ? 'var(--ok-soft)' : r.status === 'failed' ? 'var(--error-soft)' : 'var(--bg-panel)',
                              color: r.status === 'success' ? 'var(--ok)' : r.status === 'failed' ? 'var(--error)' : 'var(--text-dim)',
                            }}>
                            {STATUS_LABEL[r.status] || r.status}
                          </span>
                          <span style={{ color: 'var(--text-faint)' }}>{r.trigger} · {fmt(r.startedAt)}</span>
                        </div>
                        {r.error && <div className="text-xs mb-1" style={{ color: 'var(--error)' }}>错误：{r.error}</div>}
                        {(r.status === 'failed' || r.status === 'skipped') && (
                          <div className="mb-1">
                            {diagnosis[r.id]?.diagnosis ? (
                              <div className="text-[11px] rounded-lg p-2 mt-1"
                                style={{ background: 'var(--code-bg)', color: 'var(--text-dim)' }}>
                                <span className="font-medium" style={{ color: 'var(--accent)' }}>
                                  [{diagnosis[r.id].diagnosis.category}]
                                </span>{' '}
                                {diagnosis[r.id].diagnosis.reason}
                                <div className="mt-0.5" style={{ color: 'var(--ok)' }}>
                                  💡 {diagnosis[r.id].diagnosis.suggestion}
                                </div>
                              </div>
                            ) : (
                              <button
                                className="text-[11px] px-2 py-0.5 rounded"
                                style={{ background: 'var(--bg-panel)', color: 'var(--accent)', border: '1px solid var(--border-soft)' }}
                                disabled={diagLoading === r.id}
                                onClick={() => diagnose(r.id)}
                              >
                                {diagLoading === r.id ? '分析中...' : '🔍 诊断原因'}
                              </button>
                            )}
                          </div>
                        )}
                        {r.logs.length > 0 && (
                          <div className="space-y-0.5">
                            {r.logs.map((l, i) => (
                              <div key={i} className="text-[11px] flex gap-2" style={{ color: 'var(--text-dim)' }}>
                                <span style={{ color: l.ok === false ? 'var(--error)' : l.ok ? 'var(--ok)' : 'var(--text-faint)' }}>
                                  {l.ok === false ? '✘' : l.ok ? '✔' : '•'}
                                </span>
                                <span className="font-mono">{l.step}</span>
                                <span className="px-1 rounded" style={{ background: 'var(--code-bg)' }}>{l.type}</span>
                                {l.error ? <span style={{ color: 'var(--error)' }}>{l.error}</span> : <span className="truncate">{JSON.stringify(l.output || '').slice(0, 80)}</span>}
                                <span className="ml-auto shrink-0">{l.ms}ms</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default WorkflowsView
