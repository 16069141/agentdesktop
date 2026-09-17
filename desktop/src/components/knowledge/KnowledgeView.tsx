import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../../api'

interface KbServer {
  id: string
  name: string
  type: string
  platform: string
  base_url: string
  enabled: boolean
  has_api_key: boolean
  last_health_at?: number | null
  last_health_ok?: boolean | null
}

interface KbResult {
  title: string
  snippet: string
  source: string
  url?: string
  score?: number
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

/** 知识库：连接管理 + 检索演示（纯云端/连接式） */
const KnowledgeView: React.FC = () => {
  const [servers, setServers] = useState<KbServer[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [query, setQuery] = useState('')
  const [topics, setTopics] = useState<string[]>([])
  const [topic, setTopic] = useState('')
  const [results, setResults] = useState<KbResult[] | null>(null)
  const [searching, setSearching] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'err' | 'ok'; text: string } | null>(null)

  const load = useCallback(async () => {
    try {
      const list = await api.knowledgeServers.list()
      setServers(Array.isArray(list) ? list : [])
      setSelectedId((prev) => {
        if (prev && (list as KbServer[]).some((s) => s.id === prev)) return prev
        return (list as KbServer[])[0]?.id ?? ''
      })
    } catch (e) {
      setMsg({ kind: 'err', text: `加载知识库连接失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }, [])

  const loadTopics = useCallback(async (id: string) => {
    setTopics([])
    setTopic('')
    if (!id) return
    try {
      const r = await api.knowledgeServers.topics(id)
      const t = r?.topics
      if (Array.isArray(t) && t.length > 0) setTopics(t)
    } catch { /* 平台不支持主题，忽略 */ }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    loadTopics(selectedId)
  }, [selectedId, loadTopics])

  const search = async () => {
    if (!selectedId || !query.trim()) return
    setSearching(true)
    setMsg(null)
    try {
      const r = await api.knowledgeServers.search(selectedId, query.trim(), 5, topic || undefined)
      setResults(r.results || [])
    } catch (e) {
      setMsg({ kind: 'err', text: `检索失败：${e instanceof Error ? e.message : String(e)}` })
      setResults(null)
    } finally {
      setSearching(false)
    }
  }

  const test = async (id: string) => {
    setMsg(null)
    try {
      const r = await api.knowledgeServers.test(id)
      setMsg(r.ok ? { kind: 'ok', text: '连接正常' } : { kind: 'err', text: `连接失败：${r.error || '未知错误'}` })
      await load()
    } catch (e) {
      setMsg({ kind: 'err', text: `测试失败：${e instanceof Error ? e.message : String(e)}` })
    }
  }

  const enabled = servers.filter((s) => s.enabled)

  return (
    <div className="h-full overflow-y-auto p-6" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-1">知识库</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        连接局域网 / 互联网的知识库（RAG、LLM Wiki 等）进行检索；新增与编辑请在「设置 → 知识库连接」中操作。
      </div>

      {msg && (
        <div className="mb-4 p-3 rounded-lg text-sm" style={{
          background: msg.kind === 'err' ? 'var(--error-soft)' : 'var(--ok-soft)',
          color: msg.kind === 'err' ? 'var(--error)' : 'var(--ok)',
        }}>
          {msg.text}
        </div>
      )}

      {/* 连接列表 */}
      <div className="mb-4 space-y-2">
        {servers.length === 0 && (
          <div className="p-4 rounded-xl text-sm" style={{ background: 'var(--bg-panel)', border: '1px dashed var(--border-soft)', color: 'var(--text-faint)' }}>
            尚未配置知识库连接。前往「设置 → 知识库连接」新增 RAG / Wiki 连接，即可在此检索。
          </div>
        )}
        {servers.map((s) => (
          <div key={s.id} className="flex items-center justify-between p-3 rounded-xl"
            style={{
              background: 'var(--bg-panel)',
              border: `1px solid ${selectedId === s.id ? 'var(--primary)' : 'var(--border-soft)'}`,
            }}>
            <button className="flex-1 text-left" onClick={() => { setSelectedId(s.id); setResults(null) }}>
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{s.name}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
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
            </button>
            <button onClick={() => test(s.id)}
              className="ml-3 px-3 py-1 text-xs rounded-lg shrink-0"
              style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
              测试
            </button>
          </div>
        ))}
      </div>

      {/* 检索演示 */}
      <div className="p-4 rounded-xl" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
        <div className="font-medium mb-3">检索演示</div>
        <div className="flex gap-2">
          <select
            value={selectedId}
            onChange={(e) => { setSelectedId(e.target.value); setResults(null) }}
            className="shrink-0 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
          >
            {servers.length === 0 && <option value="">未配置连接</option>}
            {servers.map((s) => (
              <option key={s.id} value={s.id} disabled={!s.enabled}>
                {s.name}{s.enabled ? '' : '（停用）'}
              </option>
            ))}
          </select>
          {topics.length > 0 && (
            <select
              value={topic}
              onChange={(e) => { setTopic(e.target.value); setResults(null) }}
              title="按主题目录过滤检索"
              className="shrink-0 p-2 rounded-lg text-sm"
              style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
            >
              <option value="">全部主题</option>
              {topics.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          )}
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="输入检索问题，如：服务器的部署步骤是什么？"
            className="flex-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
          />
          <button
            onClick={search}
            disabled={!selectedId || !query.trim() || searching || enabled.length === 0}
            className="px-4 py-2 rounded-lg text-sm font-medium"
            style={{
              background: (!selectedId || !query.trim() || searching || enabled.length === 0) ? 'var(--bg-elev)' : 'var(--primary)',
              color: (!selectedId || !query.trim() || searching || enabled.length === 0) ? 'var(--text-faint)' : 'white',
              border: '1px solid var(--border-soft)',
              opacity: (!selectedId || !query.trim() || searching || enabled.length === 0) ? 0.5 : 1,
            }}
          >
            {searching ? '检索中…' : '检索'}
          </button>
        </div>

        {/* 结果 */}
        {results && (
          <div className="mt-4 space-y-3">
            {results.length === 0 && (
              <div className="text-sm" style={{ color: 'var(--text-faint)' }}>未检索到结果</div>
            )}
            {results.map((r, i) => (
              <div key={i} className="p-3 rounded-lg" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-sm">{r.title}</span>
                  <span className="text-[10px] shrink-0" style={{ color: 'var(--text-faint)' }}>
                    {r.source}{r.score ? ` · ${Number(r.score).toFixed(2)}` : ''}
                  </span>
                </div>
                <div className="text-sm mt-1 leading-relaxed" style={{ color: 'var(--text-dim)' }}>{r.snippet}</div>
                {r.url && (
                  <a href={r.url} target="_blank" rel="noreferrer"
                    className="text-xs mt-1 inline-block"
                    style={{ color: 'var(--accent)' }}>
                    打开原文 ↗
                  </a>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default KnowledgeView
