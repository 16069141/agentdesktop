import React, { useState, useEffect } from 'react'
import { useUiStore } from '../../store/useUiStore'
import { api } from '../../api'
import type { Conversation } from '../../types'
import parrotIcon from '../../assets/parrot-icon.png'

const ACCENTS: Record<string, string> = {
  teal: '#3FD8BE',
  violet: '#A78BFA',
  amber: '#F2B35E',
  rose: '#F07C9C',
}

interface Ws { id: string; name: string; path: string }

const Sidebar: React.FC = () => {
  const {
    theme, accent, setTheme, uiStyle, setUiStyle,
    conversations, setConversations,
    currentConversationId, setCurrentConversationId,
    currentModelId,
    chatMode,
    workspaceDir,
    setWorkspaceDir,
    workspaces,
    setWorkspaces,
  } = useUiStore()

  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const accentColor = ACCENTS[accent] || ACCENTS.teal

  // 工作模式：从工作空间列表读项目（与底部选择器共用同一份 store 数据）
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  // 工作模式：左侧会话列表按当前工作目录过滤，与底部工作目录联动
  const visibleConvs =
    chatMode === 'work'
      ? conversations.filter((c) => (c.workspacePath || '') === (workspaceDir || ''))
      : conversations

  // 工作模式：按项目（工作目录）分组会话
  const projects = workspaces.map((ws) => ({
    ...ws,
    convs: conversations.filter((c) => (c.workspacePath || '') === ws.path),
  }))
  const unboundConvs = conversations.filter((c) => !c.workspacePath)

  /** 点击项目：切换工作目录并展开/折叠 */
  const openProject = (path: string) => {
    setWorkspaceDir(path)
    setExpanded((prev) => ({ ...prev, [path]: prev[path] !== true }))
  }
  const isExpanded = (path: string) => expanded[path] !== false

  /** 移除项目：仅从项目列表移除（不删磁盘目录、不删会话） */
  const removeProject = (e: React.MouseEvent, ws: Ws) => {
    e.stopPropagation()
    const next = workspaces.filter((w) => w.path !== ws.path)
    setWorkspaces(next)
    // 若移除的是当前项目，工作目录切到剩余第一个或清空
    if (workspaceDir === ws.path) {
      setWorkspaceDir(next[0]?.path || '')
    }
  }

  // 切换工作目录后，若当前会话不属于新目录，自动落到该目录下第一个会话（或空）
  useEffect(() => {
    if (chatMode !== 'work') return
    if (visibleConvs.length === 0) {
      if (currentConversationId) setCurrentConversationId(null)
      return
    }
    if (!visibleConvs.some((c) => c.id === currentConversationId)) {
      setCurrentConversationId(visibleConvs[0].id)
    }
  }, [visibleConvs, chatMode, currentConversationId, setCurrentConversationId])

  /** 开始编辑标题 */
  const startEdit = (e: React.MouseEvent, conv: Conversation) => {
    e.stopPropagation()
    setEditingId(conv.id)
    setEditingTitle(conv.title || '')
  }

  /** 保存标题 */
  const saveEdit = async () => {
    if (!editingId) return
    const newTitle = editingTitle.trim() || '未命名对话'
    const id = editingId
    setEditingId(null)
    // 乐观更新本地
    setConversations(conversations.map((c) => (c.id === id ? { ...c, title: newTitle } : c)))
    try {
      await api.conversations.update(id, { title: newTitle })
    } catch (e) {
      console.error('[Sidebar] 重命名失败:', e)
    }
  }

  /** 取消编辑 */
  const cancelEdit = () => {
    setEditingId(null)
    setEditingTitle('')
  }

  /** 新建会话 */
  const handleNewConversation = async () => {
    if (busy) return
    setBusy(true)
    try {
      const conv = (await api.conversations.create({
        title: '新对话',
        modelId: currentModelId,
        mode: chatMode,
        // 工作模式下把当前选中的工作目录绑定到新会话，形成三者联动
        workspacePath: chatMode === 'work' ? workspaceDir : '',
      })) as Conversation
      setConversations([conv, ...conversations])
      setCurrentConversationId(conv.id)
      // 新对话缓存为空，ChatView 会显示空并等待用户输入
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
      }
    } catch (e) {
      console.error('[Sidebar] 删除会话失败:', e)
    }
  }

  /** 切换会话 */
  const handleSelect = (id: string) => {
    if (id === currentConversationId) return
    setCurrentConversationId(id)
    // 不再清空 messages，ChatView 会从缓存立即显示，再后台刷新
  }

  /** 单个会话项（对话模式与工作模式树形下复用） */
  const renderConvItem = (conv: Conversation) => {
    const active = conv.id === currentConversationId
    return (
      <div
        key={conv.id}
        className="group flex items-center gap-1 rounded-lg pl-9 pr-2.5 py-1.5 mb-0.5 cursor-pointer"
        style={{
          background: active ? 'var(--bg-elev)' : 'transparent',
          borderLeft: active ? `2px solid ${accentColor}` : '2px solid transparent',
        }}
        onClick={() => handleSelect(conv.id)}
      >
        {editingId === conv.id ? (
          <input
            autoFocus
            className="text-xs flex-1 rounded px-1.5 py-0.5"
            style={{
              background: 'var(--bg-elev)',
              color: 'var(--text)',
              border: `1px solid ${accentColor}`,
              outline: 'none',
            }}
            value={editingTitle}
            onChange={(e) => setEditingTitle(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onDoubleClick={(e) => e.stopPropagation()}
            onBlur={saveEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                saveEdit()
              } else if (e.key === 'Escape') {
                e.preventDefault()
                cancelEdit()
              }
            }}
          />
        ) : (
          <span
            className="text-xs flex-1 truncate"
            style={{ color: active ? 'var(--text)' : 'var(--text-dim)' }}
            title={`${conv.title}（双击重命名）`}
            onDoubleClick={(e) => startEdit(e, conv)}
          >
            {conv.title || '未命名对话'}
          </span>
        )}
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
  }

  return (
    <aside
      className="flex flex-col h-full select-none neu-panel neu-sidebar"
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
            className="w-8 h-8 rounded-lg flex items-center justify-center overflow-hidden flex-shrink-0"
            style={{ background: 'transparent' }}
          >
            <img src={parrotIcon} alt="颤翎子" className="w-full h-full object-cover rounded-lg" />
          </div>
          <div className="min-w-0">
            <div className="font-semibold text-sm" style={{ color: 'var(--text)' }}>
              颤翎子AI助手
            </div>
            <div className="text-xs truncate" style={{ color: 'var(--text-faint)' }}>
              本地模型 · 数据不出内网
            </div>
          </div>
        </div>
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
          ＋ 新建{chatMode === 'work' ? '工作会话' : '对话'}
        </button>
      </div>

      {/* 会话列表（工作模式：项目→会话 树形；对话模式：扁平） */}
      <div className="flex-1 overflow-y-auto px-2 py-1">
        {chatMode === 'work' ? (
          <>
            <div className="px-2.5 py-1 text-xs font-medium" style={{ color: 'var(--text-faint)' }}>
              项目
            </div>
            {projects.map((ws) => {
              const activeProj = ws.path === workspaceDir
              const open = isExpanded(ws.path)
              return (
                <div key={ws.id}>
                  {/* 项目行（一级：卡片感，与二级会话区分） */}
                  <div
                    className="group flex items-center gap-2 rounded-xl px-3 py-2.5 mb-1 cursor-pointer transition-colors"
                    style={{
                      background: activeProj
                        ? 'var(--accent-soft)'
                        : 'var(--bg-elev)',
                      border: activeProj
                        ? `1px solid ${accentColor}55`
                        : '1px solid var(--border-soft)',
                    }}
                    onClick={() => openProject(ws.path)}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={activeProj ? 'var(--accent)' : 'var(--text-dim)'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                    </svg>
                    <span className="text-sm flex-1 truncate font-medium" style={{ color: activeProj ? 'var(--accent)' : 'var(--text)' }} title={ws.path}>
                      {ws.name}
                    </span>
                    {/* 移除项目（hover 出现；仅从列表移除，不删目录/会话） */}
                    <button
                      className="opacity-0 group-hover:opacity-100 p-1 rounded transition-opacity flex items-center"
                      style={{ color: 'var(--danger)', flexShrink: 0 }}
                      title="从项目列表移除（不删除磁盘文件）"
                      onClick={(e) => removeProject(e, ws)}
                    >
                      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6h14z" />
                      </svg>
                    </button>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={activeProj ? 'var(--accent)' : 'var(--text-faint)'} strokeWidth="2" style={{ flexShrink: 0 }}>
                      <polyline points={open ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
                    </svg>
                  </div>
                  {/* 项目下的会话 */}
                  {open && ws.convs.map((conv) => renderConvItem(conv))}
                </div>
              )
            })}
            {projects.length === 0 && (
              <div className="text-center py-8 text-xs" style={{ color: 'var(--text-faint)' }}>
                未添加工作目录，点底部「选择工作空间」添加
              </div>
            )}
            {/* 未绑定目录的历史会话 */}
            {unboundConvs.map((conv) => renderConvItem(conv))}
          </>
        ) : (
          <>
            {visibleConvs.map((conv) => renderConvItem(conv))}
            {visibleConvs.length === 0 && (
              <div className="text-center py-8 text-xs" style={{ color: 'var(--text-faint)' }}>
                暂无对话，点击上方按钮新建
              </div>
            )}
          </>
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

        <div className="flex items-center justify-between mb-2">
          <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
            风格
          </span>
          <div className="flex gap-1">
            <button
              className="px-2 py-0.5 rounded text-xs"
              style={{
                background: uiStyle === 'flat' ? accentColor : 'var(--bg-elev)',
                color: uiStyle === 'flat' ? 'var(--accent-ink)' : 'var(--text-dim)',
              }}
              onClick={() => setUiStyle('flat')}
            >
              ⬜ 扁平
            </button>
            <button
              className="px-2 py-0.5 rounded text-xs"
              style={{
                background: uiStyle === 'neu' ? accentColor : 'var(--bg-elev)',
                color: uiStyle === 'neu' ? 'var(--accent-ink)' : 'var(--text-dim)',
              }}
              onClick={() => setUiStyle('neu')}
            >
              🧊 新拟物
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
    </aside>
  )
}

export default Sidebar
