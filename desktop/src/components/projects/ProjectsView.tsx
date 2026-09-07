import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface Project {
  id: string
  name: string
  description: string
  owner: string
  configShare: Record<string, boolean>
  status: boolean
  createdAt: number
  updatedAt: number
  myRole?: string
  members?: Member[]
  tasks?: Task[]
  assets?: Asset[]
}

interface Member { username: string; role: string; joinedAt: number }
interface Task {
  id: string; projectId: string; title: string; description: string
  status: string; mode: string; assignee: string; createdBy: string
  createdAt: number; updatedAt: number
}
interface Asset {
  id: string; projectId: string; name: string; type: string
  content: string; knowledgeServer: string | null; createdBy: string; createdAt: number
}

const MODE_LABEL: Record<string, string> = {
  independent: '独立', shared: '分享', collaborative: '协作',
}
const STATUS_LABEL: Record<string, string> = { todo: '待办', doing: '进行中', done: '完成' }
const ROLE_LABEL: Record<string, string> = { admin: '管理员', editor: '编辑', viewer: '只读' }

const ProjectsView: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([])
  const [detail, setDetail] = useState<Project | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ name: '', description: '' })
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [memberForm, setMemberForm] = useState({ username: '', role: 'viewer' })
  const [taskForm, setTaskForm] = useState({ title: '', mode: 'shared', assignee: '' })
  const [assetForm, setAssetForm] = useState({ name: '', type: 'doc', content: '' })
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResult, setSearchResult] = useState<any[] | null>(null)
  const [sharedConfig, setSharedConfig] = useState<any | null>(null)

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await api.projects.list()
      setProjects(Array.isArray(data) ? data : [])
    } catch (e) {
      setError(`加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadList() }, [])

  const loadDetail = async (id: string) => {
    try {
      const p = await api.projects.get(id)
      setDetail(p)
      const cfg = await api.projects.sharedConfig(id)
      setSharedConfig(cfg)
    } catch (e) {
      setError(`加载项目失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const create = async () => {
    if (!form.name.trim()) return
    try {
      await api.projects.create(form)
      setShowCreate(false)
      setForm({ name: '', description: '' })
      loadList()
    } catch (e) {
      setError(`创建失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const remove = async (p: Project) => {
    if (!window.confirm(`确认删除项目「${p.name}」？成员/任务/资产将一并删除。`)) return
    try {
      await api.projects.remove(p.id)
      if (detail?.id === p.id) setDetail(null)
      loadList()
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const addMember = async () => {
    if (!detail || !memberForm.username.trim()) return
    try {
      await api.projects.addMember(detail.id, memberForm)
      setMemberForm({ username: '', role: 'viewer' })
      loadDetail(detail.id)
    } catch (e) {
      setError(`添加成员失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const addTask = async () => {
    if (!detail || !taskForm.title.trim()) return
    try {
      await api.projects.createTask(detail.id, taskForm)
      setTaskForm({ title: '', mode: 'shared', assignee: '' })
      loadDetail(detail.id)
    } catch (e) {
      setError(`创建任务失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const addAsset = async () => {
    if (!detail || !assetForm.name.trim()) return
    try {
      await api.projects.addAsset(detail.id, assetForm)
      setAssetForm({ name: '', type: 'doc', content: '' })
      loadDetail(detail.id)
    } catch (e) {
      setError(`添加资产失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const searchAssets = async () => {
    if (!detail || !searchQuery.trim()) return
    try {
      const r = await api.projects.searchAssets(detail.id, searchQuery)
      setSearchResult(r.results || [])
    } catch (e) {
      setError(`检索失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const toggleShare = async (key: string) => {
    if (!detail) return
    const next = { ...detail.configShare, [key]: !detail.configShare[key] }
    try {
      await api.projects.setConfigSharing(detail.id, next)
      loadDetail(detail.id)
    } catch (e) {
      setError(`配置共享更新失败：${e instanceof Error ? e.message : String(e)}`)
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
      <h2 className="text-lg font-semibold mb-1">项目空间</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        多人协同 · 成员三级权限（管理员/编辑/只读）· 任务三种模式（独立/分享/协作）· 资产库服务端 RAG
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}

      <div className="flex gap-6">
        {/* 左：项目列表 */}
        <div className="w-72 shrink-0">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium">项目（{projects.length}）</span>
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
              <input
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={inputStyle} placeholder="项目名称" value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
              <input
                className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
                style={inputStyle} placeholder="描述（可选）" value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
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
          ) : projects.length === 0 ? (
            <div className="rounded-xl p-6 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>暂无项目</div>
            </div>
          ) : (
            <div className="space-y-2">
              {projects.map((p) => (
                <div
                  key={p.id}
                  className="rounded-xl p-3 cursor-pointer"
                  style={{
                    background: detail?.id === p.id ? 'var(--accent-soft)' : 'var(--bg-panel)',
                    border: `1px solid ${detail?.id === p.id ? 'var(--accent)' : 'var(--border-soft)'}`,
                  }}
                  onClick={() => loadDetail(p.id)}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{p.name}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elev)', color: 'var(--text-faint)' }}>
                      {p.myRole ? ROLE_LABEL[p.myRole] || p.myRole : 'owner'}
                    </span>
                  </div>
                  {p.description && (
                    <div className="text-xs mt-1 truncate" style={{ color: 'var(--text-dim)' }}>{p.description}</div>
                  )}
                  <div className="flex gap-2 mt-1.5">
                    <button
                      className="text-xs" style={{ color: 'var(--danger)' }}
                      onClick={(e) => { e.stopPropagation(); remove(p) }}
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 右：项目详情 */}
        <div className="flex-1 min-w-0">
          {!detail ? (
            <div className="rounded-xl p-10 text-center text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border)' }}>
              <div style={{ color: 'var(--text-dim)' }}>选择左侧项目查看详情</div>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-base font-semibold">{detail.name}</span>
                  <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                    {detail.id}
                  </span>
                  <span className="ml-auto text-xs" style={{ color: 'var(--text-faint)' }}>
                    创建 {fmt(detail.createdAt)} · owner {detail.owner}
                  </span>
                </div>
                <div className="text-sm mb-3" style={{ color: 'var(--text-dim)' }}>{detail.description || '（无描述）'}</div>
                <div className="text-xs mb-1 font-medium" style={{ color: 'var(--text-dim)' }}>配置共享</div>
                <div className="flex gap-2">
                  {(['skills', 'mcp', 'agent'] as const).map((k) => (
                    <button
                      key={k}
                      className="text-xs px-2 py-0.5 rounded"
                      style={{
                        background: detail.configShare?.[k] ? 'var(--ok-soft)' : 'var(--bg-elev)',
                        color: detail.configShare?.[k] ? 'var(--ok)' : 'var(--text-dim)',
                      }}
                      onClick={() => toggleShare(k)}
                    >
                      {k === 'skills' ? 'Skills' : k === 'mcp' ? 'MCP 连接' : 'Agent 工具'} {detail.configShare?.[k] ? '共享' : '私有'}
                    </button>
                  ))}
                </div>
                {sharedConfig && (
                  <div className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
                    共享聚合：Skills {sharedConfig.skills?.length ?? 0} 项
                    {sharedConfig.connectors ? ` · 连接器 ${sharedConfig.connectors.length} 个` : ''}
                    {sharedConfig.tools ? ` · 工具 ${sharedConfig.tools.length} 项` : ''}
                  </div>
                )}
              </div>

              {/* 成员 */}
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="text-sm font-medium mb-2">成员（{detail.members?.length ?? 0}）</div>
                <div className="flex flex-wrap gap-1 mb-2">
                  {(detail.members || []).map((m) => (
                    <span key={m.username} className="text-xs px-2 py-0.5 rounded flex items-center gap-1"
                      style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}>
                      {m.username} · {ROLE_LABEL[m.role] || m.role}
                      {m.role !== 'admin' && (
                        <button style={{ color: 'var(--danger)' }}
                          onClick={() => { api.projects.removeMember(detail.id, m.username).then(() => loadDetail(detail.id)) }}>
                          ×
                        </button>
                      )}
                    </span>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input
                    className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                    style={inputStyle} placeholder="用户名" value={memberForm.username}
                    onChange={(e) => setMemberForm({ ...memberForm, username: e.target.value })}
                  />
                  <select
                    className="text-sm rounded-lg px-2 py-1 outline-none"
                    style={inputStyle} value={memberForm.role}
                    onChange={(e) => setMemberForm({ ...memberForm, role: e.target.value })}
                  >
                    <option value="viewer">只读</option>
                    <option value="editor">编辑</option>
                    <option value="admin">管理员</option>
                  </select>
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                    onClick={addMember}
                  >
                    添加
                  </button>
                </div>
              </div>

              {/* 任务 */}
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="text-sm font-medium mb-2">任务（{detail.tasks?.length ?? 0}）</div>
                <div className="flex gap-2 mb-2">
                  <input
                    className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                    style={inputStyle} placeholder="任务标题" value={taskForm.title}
                    onChange={(e) => setTaskForm({ ...taskForm, title: e.target.value })}
                  />
                  <select
                    className="text-sm rounded-lg px-2 py-1 outline-none"
                    style={inputStyle} value={taskForm.mode}
                    onChange={(e) => setTaskForm({ ...taskForm, mode: e.target.value })}
                  >
                    <option value="independent">独立</option>
                    <option value="shared">分享</option>
                    <option value="collaborative">协作</option>
                  </select>
                  <input
                    className="w-32 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                    style={inputStyle} placeholder="负责人" value={taskForm.assignee}
                    onChange={(e) => setTaskForm({ ...taskForm, assignee: e.target.value })}
                  />
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                    onClick={addTask}
                  >
                    添加
                  </button>
                </div>
                <div className="space-y-1.5">
                  {(detail.tasks || []).map((t) => (
                    <div key={t.id} className="flex items-center gap-2 text-sm rounded-lg px-3 py-2"
                      style={{ background: 'var(--bg-elev)' }}>
                      <button
                        className="text-xs px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          background: t.status === 'done' ? 'var(--ok-soft)' : t.status === 'doing' ? 'var(--warn-soft, #3a3524)' : 'var(--bg-panel)',
                          color: t.status === 'done' ? 'var(--ok)' : t.status === 'doing' ? 'var(--warn)' : 'var(--text-dim)',
                        }}
                        onClick={() => {
                          const next = t.status === 'todo' ? 'doing' : t.status === 'doing' ? 'done' : 'todo'
                          api.projects.updateTask(detail.id, t.id, { status: next }).then(() => loadDetail(detail.id))
                        }}
                      >
                        {STATUS_LABEL[t.status]}
                      </button>
                      <span className="flex-1 truncate">{t.title}</span>
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)' }}>
                        {MODE_LABEL[t.mode] || t.mode}
                      </span>
                      {t.assignee && <span className="text-xs" style={{ color: 'var(--text-faint)' }}>{t.assignee}</span>}
                      <button className="text-xs" style={{ color: 'var(--danger)' }}
                        onClick={() => { api.projects.removeTask(detail.id, t.id).then(() => loadDetail(detail.id)) }}>
                        删除
                      </button>
                    </div>
                  ))}
                  {(detail.tasks || []).length === 0 && (
                    <div className="text-xs" style={{ color: 'var(--text-faint)' }}>暂无任务</div>
                  )}
                </div>
              </div>

              {/* 资产库 */}
              <div className="rounded-xl p-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
                <div className="text-sm font-medium mb-2">资产库（{detail.assets?.length ?? 0}）· 服务端 RAG</div>
                <div className="flex gap-2 mb-2">
                  <input
                    className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                    style={inputStyle} placeholder="资产名称" value={assetForm.name}
                    onChange={(e) => setAssetForm({ ...assetForm, name: e.target.value })}
                  />
                  <select
                    className="text-sm rounded-lg px-2 py-1 outline-none"
                    style={inputStyle} value={assetForm.type}
                    onChange={(e) => setAssetForm({ ...assetForm, type: e.target.value })}
                  >
                    <option value="doc">文档</option><option value="data">数据</option>
                    <option value="image">图片</option><option value="url">链接</option>
                  </select>
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                    onClick={addAsset}
                  >
                    添加
                  </button>
                </div>
                <input
                  className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none mb-1"
                  style={inputStyle} placeholder="资产内容 / 描述（文档类可关联知识库连接后检索）"
                  value={assetForm.content}
                  onChange={(e) => setAssetForm({ ...assetForm, content: e.target.value })}
                />
                <div className="flex gap-2 mb-2">
                  <input
                    className="flex-1 text-sm rounded-lg px-2.5 py-1.5 outline-none"
                    style={inputStyle} placeholder="RAG 检索：输入问题（资产关联知识库连接时走服务端检索）"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                  <button
                    className="px-3 py-1.5 rounded-lg text-sm"
                    style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
                    onClick={searchAssets}
                  >
                    检索
                  </button>
                </div>
                {searchResult && (
                  <div className="mb-2 space-y-1">
                    <div className="text-xs" style={{ color: 'var(--text-faint)' }}>检索结果：{searchResult.length} 条</div>
                    {searchResult.map((r, i) => (
                      <div key={i} className="text-xs rounded-lg px-3 py-2" style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}>
                        <span className="font-mono" style={{ color: 'var(--accent)' }}>{r.assetName}</span>
                        {r.score !== undefined && <span className="ml-2">score {r.score}</span>}
                        <div className="mt-1 truncate">{String(r.content).slice(0, 200)}</div>
                      </div>
                    ))}
                  </div>
                )}
                <div className="flex flex-wrap gap-1.5">
                  {(detail.assets || []).map((a) => (
                    <span key={a.id} className="text-xs px-2 py-0.5 rounded flex items-center gap-1"
                      style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}>
                      {a.name} · {a.type}
                      <button style={{ color: 'var(--danger)' }}
                        onClick={() => { api.projects.removeAsset(detail.id, a.id).then(() => loadDetail(detail.id)) }}>
                        ×
                      </button>
                    </span>
                  ))}
                  {(detail.assets || []).length === 0 && (
                    <span className="text-xs" style={{ color: 'var(--text-faint)' }}>暂无资产</span>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default ProjectsView
