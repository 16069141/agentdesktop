import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../../api'

interface ToolItem {
  id: string
  name: string
  description: string
  source: string
  enabled: boolean
  requiresApproval: boolean
  lastLoadedAt?: number
  lastUsedAt?: number
}

interface KnowledgeDoc {
  id: string
  filename: string
  doc_id: string
  chunks: number
  ingested_at: number
  status: 'ready' | 'processing' | 'error'
}

const KnowledgeView: React.FC = () => {
  const [tools, setTools] = useState<ToolItem[]>([])
  const [docs, setDocs] = useState<KnowledgeDoc[]>([])
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<any[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const loadTools = useCallback(async () => {
    try {
      const data = await api.tools.list()
      setTools(data)
    } catch (e) {
      console.error('Failed to load tools:', e)
    }
  }, [])

  const loadDocs = useCallback(async () => {
    try {
      const data = await api.knowledge.listDocs?.() || []
      setDocs(data)
    } catch (e) {
      console.error('Failed to load docs:', e)
    }
  }, [])

  useEffect(() => {
    loadTools()
    loadDocs()
  }, [loadTools, loadDocs])

  const handleToggleTool = async (toolId: string, enabled: boolean) => {
    try {
      if (enabled) {
        await api.tools.enable(toolId)
      } else {
        await api.tools.disable(toolId)
      }
      await loadTools()
      setSuccess(enabled ? `已启用 ${toolId}` : `已禁用 ${toolId}`)
    } catch (e) {
      setError(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleSearch = async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    setError(null)
    setSearchResults([])
    try {
      const results = await api.knowledge.search(searchQuery, 5)
      setSearchResults(results)
    } catch (e) {
      setError(`搜索失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setIsSearching(false)
    }
  }

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploading(true)
    setError(null)
    setSuccess(null)

    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('filename', file.name)

      const result = await api.knowledge.ingest(formData)
      setSuccess(`导入成功：${file.name} (${result.chunks} chunks)`)
      await loadDocs()
    } catch (e) {
      setError(`导入失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setUploading(false)
      e.target.value = ''
    }
  }

  const handleDeleteDoc = async (docId: string) => {
    try {
      await api.knowledge.delete(docId)
      setDocs(prev => prev.filter(d => d.id !== docId))
      setSuccess(`已删除文档`)
    } catch (e) {
      setError(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const formatTime = (ts?: number) => {
    if (!ts) return '-'
    return new Date(ts).toLocaleString('zh-CN')
  }

  return (
    <div className="p-6" style={{ color: 'var(--text)' }}>
      <h2 className="text-lg font-semibold mb-4">知识库</h2>
      <div className="text-sm mb-4" style={{ color: 'var(--text-dim)' }}>
        文档导入 · 混合检索 · 引用溯源（Phase 2）
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

      {/* 工具管理 */}
      <div className="mb-6">
        <h3 className="font-medium mb-3">工具集状态</h3>
        <div className="space-y-2">
          {tools.map((tool) => (
            <div
              key={tool.id}
              className="flex items-center justify-between p-3 rounded-lg"
              style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}
            >
              <div>
                <div className="font-medium">{tool.name}</div>
                <div className="text-sm" style={{ color: 'var(--text-dim)' }}>{tool.description}</div>
                <div className="text-xs mt-1" style={{ color: 'var(--text-faint)' }}>
                  {tool.requiresApproval && '🔒 需确认 · '}
                  {tool.source === 'builtin' ? '内置' : tool.source} ·
                  最后使用：{formatTime(tool.lastUsedAt)}
                </div>
              </div>
              <button
                onClick={() => handleToggleTool(tool.id, !tool.enabled)}
                className={`px-3 py-1 rounded text-sm ${tool.enabled ? 'bg-green-500/20 text-green-500' : 'bg-gray-500/20 text-gray-400'}`}
                disabled={tool.source === 'builtin'}
                style={{ opacity: tool.source === 'builtin' ? 0.5 : 1 }}
              >
                {tool.enabled ? '已启用' : '已禁用'}
              </button>
            </div>
          ))}
        </div>
      </div>

      {/* 文档导入 */}
      <div className="mb-6">
        <h3 className="font-medium mb-3">导入文档</h3>
        <div className="p-4 rounded-lg" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}>
          <input
            type="file"
            accept=".pdf,.md,.txt,.docx"
            onChange={handleUpload}
            disabled={uploading}
            className="text-sm"
            style={{ color: 'var(--text)' }}
          />
          <div className="text-xs mt-2" style={{ color: 'var(--text-faint)' }}>
            支持格式：PDF / Markdown / TXT / Word（降级模式仅支持文本类）
          </div>
          {uploading && <div className="text-xs mt-1" style={{ color: 'var(--thinking)' }}>上传中...</div>}
        </div>
      </div>

      {/* 文档列表 */}
      <div className="mb-6">
        <h3 className="font-medium mb-3">已导入文档 ({docs.length})</h3>
        {docs.length === 0 ? (
          <div className="text-sm p-4 rounded-lg text-center" style={{ background: 'var(--bg-panel)', color: 'var(--text-faint)' }}>
            暂无文档，请先导入
          </div>
        ) : (
          <div className="space-y-2">
            {docs.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center justify-between p-3 rounded-lg"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}
              >
                <div>
                  <div className="font-medium">{doc.filename}</div>
                  <div className="text-xs" style={{ color: 'var(--text-faint)' }}>
                    {doc.chunks} chunks · {formatTime(doc.ingested_at)}
                  </div>
                </div>
                <button
                  onClick={() => handleDeleteDoc(doc.id)}
                  className="px-2 py-1 text-xs rounded"
                  style={{ background: 'var(--error-soft)', color: 'var(--error)' }}
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 搜索 */}
      <div>
        <h3 className="font-medium mb-3">检索知识库</h3>
        <div className="flex gap-2 mb-4">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="输入查询内容..."
            className="flex-1 p-2 rounded-lg text-sm"
            style={{ background: 'var(--bg-elev)', color: 'var(--text)', border: '1px solid var(--border-soft)' }}
            onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          />
          <button
            onClick={handleSearch}
            disabled={isSearching || !searchQuery.trim()}
            className="px-4 py-2 rounded-lg text-sm font-medium"
            style={{ background: 'var(--primary)', color: 'white', opacity: isSearching || !searchQuery.trim() ? 0.5 : 1 }}
          >
            {isSearching ? '搜索中...' : '搜索'}
          </button>
        </div>

        {searchResults.length > 0 && (
          <div className="space-y-3">
            {searchResults.map((result, idx) => (
              <div
                key={idx}
                className="p-4 rounded-lg"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border-soft)' }}
              >
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-xs px-2 py-0.5 rounded" style={{ background: 'var(--bg-elev)' }}>
                    {result.retrieval_method}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
                    相关度: {result.score?.toFixed(3)}
                  </span>
                </div>
                <div className="text-xs mb-1" style={{ color: 'var(--text-dim)' }}>
                  来源：{result.source} · 文档 ID: {result.doc_id}
                </div>
                <div className="text-sm" style={{ color: 'var(--text)' }}>
                  {result.snippet}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default KnowledgeView
