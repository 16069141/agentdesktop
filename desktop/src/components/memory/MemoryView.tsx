import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface MemoryItem {
  id: number
  kind: 'preference' | 'fact' | 'conclusion'
  content: string
  source_conversation?: string
  created_at: number
  updated_at: number
  hit_count: number
}

const KIND_META: Record<string, { label: string; color: string }> = {
  preference: { label: '偏好', color: '#8b5cf6' },
  fact: { label: '事实', color: '#3b82f6' },
  conclusion: { label: '结论', color: '#f59e0b' },
}

const MemoryView: React.FC = () => {
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [total, setTotal] = useState(0)
  const [enabled, setEnabled] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [addKind, setAddKind] = useState<'preference' | 'fact' | 'conclusion'>('preference')
  const [addText, setAddText] = useState('')
  const [toast, setToast] = useState('')

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2500)
  }

  const load = useCallback(async () => {
    try {
      const [m, s] = await Promise.all([api.memory.list(), api.settings.get()])
      setMemories(m.memories || [])
      setTotal(m.total ?? (m.memories || []).length)
      setEnabled(s.memory_enabled !== false)
      setError('')
    } catch (e) {
      setError(`加载记忆失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const toggleEnabled = async () => {
    const next = !enabled
    setEnabled(next)
    try {
      await api.settings.update({ memory_enabled: next })
      showToast(next ? '已开启长期记忆（对话后自动提取，检索注入）' : '已关闭长期记忆（已有记忆保留）')
    } catch (e) {
      setEnabled(!next)
      showToast(`开关保存失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleAdd = async () => {
    const text = addText.trim()
    if (!text) return
    try {
      const res = await api.memory.add({ kind: addKind, content: text })
      setAddText('')
      showToast(res.dup ? '已添加（与已有记忆合并）' : '已添加')
      void load()
    } catch (e) {
      showToast(`添加失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleDelete = async (item: MemoryItem) => {
    if (!window.confirm(`删除这条记忆？\n「${item.content}」`)) return
    try {
      await api.memory.remove(item.id)
      showToast('已删除')
      void load()
    } catch (e) {
      showToast(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleClear = async () => {
    if (!window.confirm('清空全部记忆？此操作不可恢复。')) return
    try {
      const res = await api.memory.clear()
      showToast(`已清空 ${res.cleared} 条记忆`)
      void load()
    } catch (e) {
      showToast(`清空失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-lg font-semibold">记忆（长期记忆）</h2>
        <button
          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
          style={{
            background: enabled ? 'var(--accent)' : 'var(--bg-elev)',
            color: enabled ? 'var(--accent-ink)' : 'var(--text-faint)',
            border: '1px solid var(--border-soft)',
          }}
          onClick={toggleEnabled}
        >
          {enabled ? '● 已开启' : '○ 已关闭'}
        </button>
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--text-faint)' }}>
        跨会话记住你的偏好、项目事实和重要结论；对话时自动检索相关记忆注入上下文。
        这里的每一条都可见、可删除——你随时掌握 Agent 记住了什么。当前共 {total} 条。
      </p>

      {error && (
        <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
      {toast && (
        <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}>
          {toast}
        </div>
      )}

      {/* 手动添加 */}
      <div className="mb-5 rounded-xl px-4 py-3" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
        <div className="text-xs font-semibold mb-2" style={{ color: 'var(--text-dim)' }}>
          手动添加记忆
        </div>
        <div className="flex gap-2 mb-2">
          <select
            value={addKind}
            onChange={(e) => setAddKind(e.target.value as typeof addKind)}
            className="px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          >
            <option value="preference">偏好</option>
            <option value="fact">事实</option>
            <option value="conclusion">结论</option>
          </select>
          <input
            value={addText}
            onChange={(e) => setAddText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
            placeholder="如：用户是前端工程师，主用 TypeScript"
            className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          />
          <button
            className="px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
            onClick={handleAdd}
            disabled={!addText.trim()}
          >
            添加
          </button>
        </div>
      </div>

      {/* 列表 */}
      {loading ? (
        <div className="text-sm py-8 text-center" style={{ color: 'var(--text-faint)' }}>加载中…</div>
      ) : memories.length === 0 ? (
        <div className="text-sm py-10 text-center" style={{ color: 'var(--text-faint)' }}>
          还没有记忆。多聊几轮后，Agent 会自动记住值得长期保留的信息；也可以在上面手动添加。
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {memories.map((m) => {
            const meta = KIND_META[m.kind] || { label: m.kind, color: '#6b7280' }
            return (
              <div
                key={m.id}
                className="flex items-start gap-3 px-4 py-3 rounded-xl"
                style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
              >
                <span
                  className="flex-shrink-0 mt-0.5 text-[10px] font-bold px-1.5 py-0.5 rounded"
                  style={{ background: `${meta.color}22`, color: meta.color }}
                >
                  {meta.label}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-sm" style={{ color: 'var(--text)' }}>{m.content}</div>
                  <div className="text-[10px] mt-1" style={{ color: 'var(--text-faint)' }}>
                    {new Date(m.updated_at * 1000).toLocaleString()} · 命中 {m.hit_count} 次
                    {m.source_conversation === 'manual' ? ' · 手动添加' : ''}
                  </div>
                </div>
                <button
                  className="flex-shrink-0 px-2 py-1 rounded-md text-[11px]"
                  style={{ background: 'var(--danger)', color: '#fff' }}
                  onClick={() => handleDelete(m)}
                >
                  删除
                </button>
              </div>
            )
          })}
        </div>
      )}

      {memories.length > 0 && (
        <div className="mt-4 text-right">
          <button
            className="px-3 py-1.5 rounded-lg text-xs"
            style={{ background: 'transparent', border: '1px solid var(--border-soft)', color: 'var(--text-faint)' }}
            onClick={handleClear}
          >
            清空全部记忆
          </button>
        </div>
      )}
    </div>
  )
}

export default MemoryView
