import React, { useState } from 'react'
import { useUiStore } from '../../store/useUiStore'
import { api } from '../../api'
import type { Conversation } from '../../types'

const ACCENTS: Record<string, string> = {
  teal: '#3FD8BE',
  violet: '#A78BFA',
  amber: '#F2B35E',
  rose: '#F07C9C',
}

const Sidebar: React.FC = () => {
  const {
    theme, accent, setTheme,
    conversations, setConversations,
    currentConversationId, setCurrentConversationId,
    models, currentModelId, setCurrentModelId,
    setMessages,
  } = useUiStore()

  const [busy, setBusy] = useState(false)
  const [publicNotice, setPublicNotice] = useState<string | null>(null)
  const accentColor = ACCENTS[accent] || ACCENTS.teal

  /** 新建会话 */
  const handleNewConversation = async () => {
    if (busy) return
    setBusy(true)
    try {
      const conv = (await api.conversations.create({
        title: '新对话',
        modelId: currentModelId,
      })) as Conversation
      setConversations([conv, ...conversations])
      setCurrentConversationId(conv.id)
      setMessages([])
    } catch (e) {
      console.error('[Sidebar] 新建会话失败:', e)
    } finally {
      setBusy(false)
    }
  }

  /** 删除会话（级联删除消息在后端完成） */
  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    try {
      await api.conversations.delete(id)
      const rest = conversations.filter((c) => c.id !== id)
      setConversations(rest)
      if (currentConversationId === id) {
        setCurrentConversationId(rest[0]?.id ?? null)
        setMessages([])
      }
    } catch (e) {
      console.error('[Sidebar] 删除会话失败:', e)
    }
  }

  /** 切换会话 */
  const handleSelect = (id: string) => {
    if (id === currentConversationId) return
    setCurrentConversationId(id)
    setMessages([])
  }

  /** 切换模型；选中公网模型时弹出知情提示 */
  const handleModelChange = (modelId: string) => {
    const model = models.find((m) => m.id === modelId)
    setCurrentModelId(modelId)
    if (model?.isPublic) {
      setPublicNotice(
        `「${model.name}」为互联网商业大模型。\n使用该模型时，你输入的提示词与上下文将经由公网发送至模型服务商，不再处于私有域内。请确认内容不涉及敏感信息。`
      )
    } else {
      setPublicNotice(null)
    }
  }

  return (
    <aside
      className="flex flex-col h-full select-none"
      style={{
        width: '264px',
        flex: '0 0 264px',
        background: 'var(--bg-panel)',
        backdropFilter: 'var(--glass)',
        WebkitBackdropFilter: 'var(--glass)',
        borderRight: '1px solid var(--border-soft)',
      }}
    >
      {/* 应用标识 */}
      <div className="p-4 border-b" style={{ borderColor: 'var(--border-soft)' }}>
        <div className="flex items-center gap-2">
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center text-base font-bold"
            style={{ background: `${accentColor}22`, color: accentColor }}
          >
            P
          </div>
          <div className="min-w-0">
            <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
              PrivateAI
            </div>
            <div className="text-xs truncate" style={{ color: 'var(--text-faint)' }}>
              私有域 · 本地模型 · 数据不出内网
            </div>
          </div>
        </div>
      </div>

      {/* 模型选择 */}
      <div className="px-4 py-3 border-b" style={{ borderColor: 'var(--border-soft)' }}>
        <div className="text-xs mb-1.5" style={{ color: 'var(--text-faint)' }}>
          当前模型
        </div>
        <select
          className="w-full text-sm rounded-lg px-2.5 py-1.5 outline-none"
          style={{
            background: 'var(--surf-input)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
          }}
          value={currentModelId}
          onChange={(e) => handleModelChange(e.target.value)}
        >
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
              {m.isPublic ? '（公网）' : ''}
            </option>
          ))}
        </select>
        {models.find((m) => m.id === currentModelId)?.isPublic && (
          <div
            className="mt-2 text-xs px-2 py-1 rounded"
            style={{ background: 'rgba(242,179,94,0.14)', color: 'var(--warn)' }}
          >
            ⚠ 公网模型：数据将离开本机
          </div>
        )}
      </div>

      {/* 新建对话 */}
      <div className="px-4 py-2">
        <button
          className="w-full flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium transition-opacity disabled:opacity-50"
          style={{
            background: `${accentColor}18`,
            color: accentColor,
            border: `1px solid ${accentColor}33`,
          }}
          onClick={handleNewConversation}
          disabled={busy}
        >
          ＋ 新对话
        </button>
      </div>

      {/* 会话列表 */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        {conversations.map((conv) => {
          const active = conv.id === currentConversationId
          return (
            <div
              key={conv.id}
              className="group flex items-center gap-1 rounded-lg px-2.5 py-2 mb-0.5 cursor-pointer"
              style={{
                background: active ? 'var(--bg-elev)' : 'transparent',
                borderLeft: active ? `2px solid ${accentColor}` : '2px solid transparent',
              }}
              onClick={() => handleSelect(conv.id)}
            >
              <span
                className="text-xs flex-1 truncate"
                style={{ color: active ? 'var(--text)' : 'var(--text-dim)' }}
                title={conv.title}
              >
                {conv.title || '未命名对话'}
              </span>
              <button
                className="opacity-0 group-hover:opacity-100 text-xs px-1 rounded transition-opacity"
                style={{ color: 'var(--danger)' }}
                title="删除会话"
                onClick={(e) => handleDelete(e, conv.id)}
              >
                ×
              </button>
            </div>
          )
        })}
        {conversations.length === 0 && (
          <div className="text-center py-8 text-xs" style={{ color: 'var(--text-faint)' }}>
            暂无对话，点击上方按钮新建
          </div>
        )}
      </div>

      {/* 底部：安全状态 + 主题 */}
      <div className="p-4 border-t" style={{ borderColor: 'var(--border-soft)' }}>
        <div className="text-xs mb-3" style={{ color: 'var(--text-faint)' }}>
          🔒 已加密 · 仅本地 · 127.0.0.1
        </div>

        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
            主题
          </span>
          <div className="flex gap-1">
            <button
              className="px-2 py-0.5 rounded text-xs"
              style={{
                background: theme === 'dark' ? accentColor : 'var(--bg-elev)',
                color: theme === 'dark' ? 'var(--accent-ink)' : 'var(--text-dim)',
              }}
              onClick={() => setTheme('dark')}
            >
              🌙 深色
            </button>
            <button
              className="px-2 py-0.5 rounded text-xs"
              style={{
                background: theme === 'light' ? accentColor : 'var(--bg-elev)',
                color: theme === 'light' ? 'var(--accent-ink)' : 'var(--text-dim)',
              }}
              onClick={() => setTheme('light')}
            >
              ☀️ 浅色
            </button>
          </div>
        </div>

        <div className="flex gap-2 justify-center">
          {Object.entries(ACCENTS).map(([name, color]) => (
            <button
              key={name}
              className="w-4 h-4 rounded-full transition-transform"
              style={{
                background: color,
                boxShadow: accent === name ? `0 0 0 2px var(--bg), 0 0 0 4px ${color}` : 'none',
              }}
              title={name}
              onClick={() => setTheme(theme, name as 'teal' | 'violet' | 'amber' | 'rose')}
            />
          ))}
        </div>
      </div>

      {/* 公网模型知情提示弹窗 */}
      {publicNotice && (
        <div
          className="fixed inset-0 flex items-center justify-center z-50"
          style={{ background: 'rgba(0,0,0,0.45)' }}
          onClick={() => setPublicNotice(null)}
        >
          <div
            className="max-w-sm rounded-2xl p-5"
            style={{
              background: 'var(--bg)',
              border: '1px solid var(--border)',
              boxShadow: '0 20px 60px rgba(0,0,0,0.35)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="font-semibold mb-2" style={{ color: 'var(--warn)' }}>
              ⚠ 公网模型知情提示
            </div>
            <div
              className="text-sm whitespace-pre-wrap leading-relaxed mb-4"
              style={{ color: 'var(--text-dim)' }}
            >
              {publicNotice}
            </div>
            <button
              className="w-full py-2 rounded-lg text-sm font-medium"
              style={{ background: 'var(--warn)', color: '#1B2434' }}
              onClick={() => setPublicNotice(null)}
            >
              我已了解，继续使用该模型
            </button>
          </div>
        </div>
      )}
    </aside>
  )
}

export default Sidebar
