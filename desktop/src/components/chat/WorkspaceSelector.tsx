import React, { useState, useEffect, useRef } from 'react'

interface Workspace {
  id: string
  name: string
  path: string
}

const STORAGE_KEY = 'privateai_workspaces'
const CURRENT_KEY = 'privateai_current_workspace'

/** 从 localStorage 加载工作空间列表 */
const loadWorkspaces = (): Workspace[] => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Workspace[]) : []
  } catch {
    return []
  }
}

/** 保存工作空间列表到 localStorage */
const saveWorkspaces = (list: Workspace[]) => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(list))
}

const WorkspaceSelector: React.FC = () => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [currentId, setCurrentId] = useState<string>('')
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const list = loadWorkspaces()
    setWorkspaces(list)
    const saved = localStorage.getItem(CURRENT_KEY)
    if (saved && list.find((w) => w.id === saved)) {
      setCurrentId(saved)
    } else if (list.length > 0) {
      setCurrentId(list[0].id)
      localStorage.setItem(CURRENT_KEY, list[0].id)
    }
  }, [])

  /** 点击外部关闭下拉 */
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const current = workspaces.find((w) => w.id === currentId)

  /** 切换工作空间 */
  const selectWorkspace = (id: string) => {
    setCurrentId(id)
    localStorage.setItem(CURRENT_KEY, id)
    setOpen(false)
  }

  /** 添加工作空间（调用 electron 文件夹选择对话框） */
  const addWorkspace = async () => {
    try {
      // main 进程 open-file-dialog handler 直接返回 filePaths 数组或 null
      const result: string[] | null = await (window as any).electronAPI?.openFileDialog({
        title: '选择工作空间文件夹',
        properties: ['openDirectory', 'createDirectory'],
      })
      if (!result || !Array.isArray(result) || result.length === 0) return
      const path = result[0]
      const name = path.split('/').pop() || path
      // 去重：同一路径不重复添加
      if (workspaces.find((w) => w.path === path)) {
        setOpen(false)
        return
      }
      const ws: Workspace = {
        id: `ws-${Date.now()}`,
        name,
        path,
      }
      const next = [...workspaces, ws]
      setWorkspaces(next)
      saveWorkspaces(next)
      setCurrentId(ws.id)
      localStorage.setItem(CURRENT_KEY, ws.id)
      setOpen(false)
    } catch (e) {
      console.error('[Workspace] 添加失败:', e)
    }
  }

  /** 删除工作空间 */
  const removeWorkspace = (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    const next = workspaces.filter((w) => w.id !== id)
    setWorkspaces(next)
    saveWorkspaces(next)
    if (currentId === id) {
      const newCurrent = next[0]?.id ?? ''
      setCurrentId(newCurrent)
      if (newCurrent) localStorage.setItem(CURRENT_KEY, newCurrent)
      else localStorage.removeItem(CURRENT_KEY)
    }
  }

  const btnStyle: React.CSSProperties = {
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-soft)',
    color: current ? 'var(--text)' : 'var(--text-dim)',
  }

  return (
    <div className="relative" ref={panelRef}>
      <button
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors max-w-[180px]"
        style={btnStyle}
        onClick={() => setOpen(!open)}
        title={current?.path || '选择工作空间'}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
        <span className="truncate">{current ? current.name : '选择工作空间'}</span>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
          <polyline points={open ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
        </svg>
      </button>

      {/* 下拉面板 */}
      {open && (
        <div
          className="absolute bottom-full left-0 mb-1.5 w-64 rounded-xl z-20 overflow-hidden"
          style={{
            background: 'var(--bg-elev)',
            border: '1px solid var(--border)',
            boxShadow: '0 8px 24px rgba(0,0,0,0.2)',
          }}
        >
          <div className="px-3 py-2 text-xs font-medium" style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border-soft)' }}>
            工作空间
          </div>

          <div className="max-h-52 overflow-y-auto">
            {workspaces.length === 0 && (
              <div className="px-3 py-4 text-xs text-center" style={{ color: 'var(--text-faint)' }}>
                暂无工作空间，点击下方添加
              </div>
            )}
            {workspaces.map((ws) => (
              <div
                key={ws.id}
                className="group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors"
                style={{
                  background: ws.id === currentId ? 'var(--accent-soft)' : 'transparent',
                }}
                onClick={() => selectWorkspace(ws.id)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-faint)', flexShrink: 0 }}>
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                </svg>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate" style={{ color: ws.id === currentId ? 'var(--accent)' : 'var(--text)' }}>
                    {ws.name}
                  </div>
                  <div className="text-[10px] truncate" style={{ color: 'var(--text-faint)' }} title={ws.path}>
                    {ws.path}
                  </div>
                </div>
                {ws.id === currentId && (
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                )}
                <button
                  className="opacity-0 group-hover:opacity-100 p-1 rounded transition-opacity"
                  style={{ color: 'var(--danger)' }}
                  onClick={(e) => removeWorkspace(e, ws.id)}
                  title="移除工作空间"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="18" y1="6" x2="6" y2="18" />
                    <line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </div>
            ))}
          </div>

          <div style={{ borderTop: '1px solid var(--border-soft)' }}>
            <button
              className="w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors"
              style={{ color: 'var(--accent)' }}
              onClick={addWorkspace}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              添加工作空间
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default WorkspaceSelector
