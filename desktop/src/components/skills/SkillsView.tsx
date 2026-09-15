import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface SkillItem {
  id: string
  name: string
  version: string
  description: string
  kind: string
  permissionLevel: string
  source: string
  sourceUrl: string
  manifest?: Record<string, any>
  enabled: boolean
  installedAt: number
}

const LEVEL_COLOR: Record<string, string> = {
  P1: 'var(--ok)',
  P2: 'var(--accent)',
  P3: 'var(--warn)',
  P4: 'var(--danger)',
}

const LEVEL_DESC: Record<string, string> = {
  P1: '仅纯函数，无文件/网络/系统调用',
  P2: '受限文件系统（工作目录），无网络',
  P3: '文件只读 + 受限网络白名单',
  P4: '系统调用级，需审批',
}

const SkillsView: React.FC = () => {
  const [skills, setSkills] = useState<SkillItem[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [installSource, setInstallSource] = useState<'local_dir' | 'zip'>('local_dir')
  const [installPath, setInstallPath] = useState('')
  const [stats, setStats] = useState<{
    total_local: number
    total_online: number
    installed_count: number
    source_distribution: Record<string, number>
    recent_installs: { id: string; name: string; version: string; source: string; installedAt: number }[]
  } | null>(null)
  const [marketItems, setMarketItems] = useState<any[]>([])
  const [marketSearch, setMarketSearch] = useState('')
  const [installedIds, setInstalledIds] = useState<Set<string>>(new Set())
  const [installing, setInstalling] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [data, market, marketStats] = await Promise.all([
        api.skills.list(),
        api.skills.market(),
        api.skills.marketStats(),
      ])
      const installed = Array.isArray(data) ? data : []
      setSkills(installed)
      // 市场 = 本地包（data/skill_market）+ 在线市场（skill_market_urls / SKILL_HUB_URL）
      const local = Array.isArray(market?.items) ? market.items : []
      const online = Array.isArray(market?.online) ? market.online : []
      // 按 id 去重（本地/在线两路可能同 id，后端已分路，这里做兜底防御）
      const seen = new Set<string>()
      const merged = [...local, ...online].filter((m) => {
        if (!m || !m.id) return false
        if (seen.has(m.id)) return false
        seen.add(m.id)
        return true
      })
      setMarketItems(merged)
      setInstalledIds(new Set(installed.map((s) => s.id)))
      setStats(marketStats)
    } catch (e) {
      setError(`加载技能失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [])

  const install = async () => {
    if (!installPath.trim()) return
    setError(null)
    try {
      const res = await api.skills.install({ source: installSource, path: installPath.trim() })
      if (!res.ok) {
        const issues = (res.issues || []).join('；')
        setError(`安装失败：${res.error || ''}${issues ? `（${issues}）` : ''}`)
      } else {
        setInstallPath('')
        load()
      }
    } catch (e) {
      setError(`安装失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const toggle = async (s: SkillItem) => {
    try {
      if (s.enabled) await api.skills.disable(s.id)
      else await api.skills.enable(s.id)
      load()
    } catch (e) {
      setError(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const installMarket = async (item: any) => {
    setInstalling(item.id)
    setError(null)
    try {
      const res = await api.skills.install({ source: 'market', market_id: item.id })
      if (!res.ok) {
        const issues = (res.issues || []).join('；')
        setError(`安装失败：${res.error || ''}${issues ? `（${issues}）` : ''}`)
      } else {
        load()
      }
    } catch (e) {
      setError(`安装失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setInstalling(null)
    }
  }

  const remove = async (s: SkillItem) => {
    if (!window.confirm(`确认卸载技能「${s.name}」？`)) return
    try {
      await api.skills.remove(s.id)
      load()
    } catch (e) {
      setError(`卸载失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  // 市场检索（客户端过滤：名称 / 描述 / ID）
  const q = marketSearch.trim().toLowerCase()
  const filteredMarket = q
    ? marketItems.filter((m) =>
        [m.name, m.description, m.id].some(
          (v) => typeof v === 'string' && v.toLowerCase().includes(q),
        ),
      )
    : marketItems

  return (
    <div className="p-6 overflow-y-auto h-full" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">技能（Skills）</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        标准包结构（SKILL.md + manifest.json）· 四级安全等级 P1–P4 · 本地市场 + SkillHub/ClawHub 在线市场
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{ background: 'var(--error-soft)', color: 'var(--error)' }}>
          {error}
        </div>
      )}

      {/* P5：技能市场运营统计 */}
      {stats && (
        <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <div className="font-medium mb-2 text-sm">技能市场运营</div>
          <div className="flex gap-3 flex-wrap text-xs">
            <div className="px-3 py-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <div className="text-lg font-semibold" style={{ color: 'var(--accent)' }}>{stats.total_local}</div>
              <div style={{ color: 'var(--text-faint)' }}>本地市场包</div>
            </div>
            <div className="px-3 py-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <div className="text-lg font-semibold" style={{ color: 'var(--accent)' }}>{stats.total_online}</div>
              <div style={{ color: 'var(--text-faint)' }}>在线市场包</div>
            </div>
            <div className="px-3 py-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <div className="text-lg font-semibold" style={{ color: 'var(--ok)' }}>{stats.installed_count}</div>
              <div style={{ color: 'var(--text-faint)' }}>已安装技能</div>
            </div>
            <div className="flex-1 min-w-40 px-3 py-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <div className="mb-1" style={{ color: 'var(--text-faint)' }}>来源分布</div>
              <div className="flex flex-wrap gap-1">
                {Object.entries(stats.source_distribution).map(([k, v]) => (
                  <span key={k} className="px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--code-bg)', color: 'var(--text-dim)' }}>
                    {k}×{v}
                  </span>
                ))}
              </div>
            </div>
            <div className="flex-1 min-w-40 px-3 py-2 rounded-lg" style={{ background: 'var(--bg-elev)' }}>
              <div className="mb-1" style={{ color: 'var(--text-faint)' }}>最近安装</div>
              <div className="space-y-0.5">
                {stats.recent_installs.length === 0 && <span style={{ color: 'var(--text-faint)' }}>暂无</span>}
                {stats.recent_installs.map((r) => (
                  <div key={r.id} className="flex justify-between gap-2">
                    <span style={{ color: 'var(--text-dim)' }}>{r.name}</span>
                    <span className="font-mono" style={{ color: 'var(--text-faint)' }}>
                      {new Date(r.installedAt).toLocaleString('zh-CN', { hour12: false })}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 技能市场（本地市场 + SkillHub/ClawHub 在线市场） */}
      {marketItems.length > 0 && (
        <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <div className="flex items-center gap-3 mb-3 flex-wrap">
            <div className="font-medium text-sm">技能市场</div>
            <span className="text-xs" style={{ color: 'var(--text-faint)' }}>共 {marketItems.length} 个技能包</span>
            <input
              className="flex-1 min-w-40 text-sm rounded-lg px-2.5 py-1.5 outline-none"
              style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
              placeholder="搜索技能（名称 / 描述 / ID）"
              value={marketSearch}
              onChange={(e) => setMarketSearch(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            {filteredMarket.map((m) => {
                const installed = installedIds.has(m.id)
                return (
                  <div key={`${m.source}-${m.id}`} className="flex items-center gap-2 text-xs p-2 rounded" style={{ background: 'var(--bg-elev)' }}>
                    <span className="font-medium" style={{ color: 'var(--text)' }}>{m.name}</span>
                    <span className="font-mono" style={{ color: 'var(--text-faint)' }}>v{m.version}</span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: `${LEVEL_COLOR[m.security_level] || 'var(--accent)'}22`, color: LEVEL_COLOR[m.security_level] || 'var(--accent)' }}>
                      {m.security_level}
                    </span>
                    <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--code-bg)', color: 'var(--text-dim)' }}>
                      {m.source === 'online_market' ? '在线' : '本地'}
                    </span>
                    <span className="flex-1 truncate" style={{ color: 'var(--text-faint)' }}>{m.description}</span>
                    <button
                      className="px-2 py-0.5 rounded"
                      style={{
                        background: installed ? 'var(--ok-soft)' : 'var(--accent-soft)',
                        color: installed ? 'var(--ok)' : 'var(--accent)',
                      }}
                      disabled={installed || installing === m.id}
                      onClick={() => installMarket(m)}
                    >
                      {installing === m.id ? '安装中...' : installed ? '已安装' : '安装'}
                    </button>
                  </div>
                )
              })}
            {filteredMarket.length === 0 && (
              <div className="text-xs" style={{ color: 'var(--text-faint)' }}>没有匹配的技能包</div>
            )}
          </div>
          <div className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
            市场地址：settings.json 的 <span className="font-mono">skill_market_urls</span>（或环境变量 SKILL_HUB_URL / CLAWHUB_URL），多个地址自动合并。
          </div>
        </div>
      )}

      {/* 安装 */}
      <div className="p-4 rounded-xl mb-4" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
        <div className="font-medium mb-3 text-sm">安装技能（企业私有部署）</div>
        <div className="flex gap-2 flex-wrap">
          <select
            className="text-sm rounded-lg px-2.5 py-1.5 outline-none"
            style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
            value={installSource}
            onChange={(e) => setInstallSource(e.target.value as any)}
          >
            <option value="local_dir">本地目录</option>
            <option value="zip">ZIP 包</option>
          </select>
          <input
            className="flex-1 min-w-48 text-sm rounded-lg px-2.5 py-1.5 outline-none"
            style={{ background: 'var(--surf-input)', color: 'var(--text)', border: '1px solid var(--border)' }}
            placeholder={installSource === 'local_dir' ? 'Skill 包目录绝对路径（含 manifest.json）' : 'ZIP 文件绝对路径'}
            value={installPath}
            onChange={(e) => setInstallPath(e.target.value)}
          />
          <button
            className="px-3 py-1.5 rounded-lg text-sm"
            style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}
            onClick={install}
          >
            安装
          </button>
        </div>
        <div className="mt-2 text-xs" style={{ color: 'var(--text-faint)' }}>
          本地目录 / ZIP 私有部署；市场包（本地 + 在线）可直接安装，安装前自动做 manifest 校验与 P1–P4 安全策略检查。
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div style={{ color: 'var(--text-faint)' }}>加载中...</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {skills.map((s) => (
            <div
              key={s.id}
              className="rounded-xl p-4"
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}
            >
              <div className="flex items-center gap-2 mb-1">
                <span className="font-medium text-sm">{s.name}</span>
                <span className="font-mono text-xs" style={{ color: 'var(--text-faint)' }}>v{s.version}</span>
                <span
                  className="px-1.5 py-0.5 rounded text-xs font-mono"
                  style={{ background: `${LEVEL_COLOR[s.permissionLevel] || 'var(--accent)'}22`, color: LEVEL_COLOR[s.permissionLevel] || 'var(--accent)' }}
                  title={LEVEL_DESC[s.permissionLevel]}
                >
                  {s.permissionLevel}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elev)', color: 'var(--text-faint)' }}>
                  {s.source}
                </span>
                <span className="ml-auto flex items-center gap-2">
                  <button
                    className="text-xs px-2 py-0.5 rounded"
                    style={{
                      background: s.enabled ? 'var(--ok-soft)' : 'var(--bg-elev)',
                      color: s.enabled ? 'var(--ok)' : 'var(--text-dim)',
                    }}
                    onClick={() => toggle(s)}
                  >
                    {s.enabled ? '已启用' : '已停用'}
                  </button>
                  {s.source !== 'builtin' && (
                    <button className="text-xs" style={{ color: 'var(--danger)' }} onClick={() => remove(s)}>
                      卸载
                    </button>
                  )}
                </span>
              </div>
              <div className="text-xs mb-2" style={{ color: 'var(--text-dim)' }}>{s.description}</div>
              <div className="text-xs" style={{ color: 'var(--text-faint)' }}>
                等级说明：{LEVEL_DESC[s.permissionLevel] || s.permissionLevel}
              </div>
              {s.manifest?.tools && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {(s.manifest.tools as any[]).map((t, i) => (
                    <span key={i} className="text-xs px-1.5 py-0.5 rounded font-mono" style={{ background: 'var(--code-bg)', color: 'var(--accent)' }}>
                      {t.name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default SkillsView
