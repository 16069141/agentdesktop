import React, { useState, useEffect, useRef } from 'react'
import { api } from '../../api'

interface Skill {
  id: string
  name: string
  description?: string
  enabled: boolean
  version?: string
}

interface SkillSelectorProps {
  /** 点击"管理技能"时的导航回调 */
  onManage?: () => void
}

const SkillSelector: React.FC<SkillSelectorProps> = ({ onManage }) => {
  const [skills, setSkills] = useState<Skill[]>([])
  const [open, setOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  const loadSkills = async () => {
    try {
      const list = (await api.skills.list()) as Skill[]
      setSkills(Array.isArray(list) ? list : [])
    } catch (e) {
      console.error('[SkillSelector] 加载失败:', e)
    }
  }

  useEffect(() => {
    loadSkills()
  }, [])

  /** 点击外部关闭 */
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

  const enabledCount = skills.filter((s) => s.enabled).length

  /** 切换技能启用状态 */
  const toggleSkill = async (skill: Skill) => {
    if (busyId) return
    setBusyId(skill.id)
    try {
      if (skill.enabled) {
        await api.skills.disable(skill.id)
      } else {
        await api.skills.enable(skill.id)
      }
      setSkills((prev) =>
        prev.map((s) => (s.id === skill.id ? { ...s, enabled: !s.enabled } : s))
      )
    } catch (e) {
      console.error('[SkillSelector] 切换失败:', e)
    } finally {
      setBusyId(null)
    }
  }

  const btnStyle: React.CSSProperties = {
    background: 'var(--bg-panel)',
    border: '1px solid var(--border-soft)',
    color: 'var(--text-dim)',
  }

  return (
    <div className="relative" ref={panelRef}>
      <button
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-colors"
        style={btnStyle}
        onClick={() => setOpen(!open)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
        </svg>
        技能
        {enabledCount > 0 && (
          <span
            className="min-w-[16px] h-4 px-1 rounded-full text-[10px] font-bold flex items-center justify-center"
            style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
          >
            {enabledCount}
          </span>
        )}
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
          <polyline points={open ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
        </svg>
      </button>

      {/* 下拉面板 */}
      {open && (
        <div
          className="absolute bottom-full left-0 mb-1.5 w-72 rounded-xl z-20 overflow-hidden"
          style={{
            background: 'var(--bg-elev)',
            border: '1px solid var(--border)',
            boxShadow: '0 8px 24px rgba(0,0,0,0.2)',
          }}
        >
          <div
            className="px-3 py-2 text-xs font-medium flex items-center justify-between"
            style={{ color: 'var(--text-faint)', borderBottom: '1px solid var(--border-soft)' }}
          >
            <span>已安装技能</span>
            <span>{enabledCount}/{skills.length} 已启用</span>
          </div>

          <div className="max-h-64 overflow-y-auto">
            {skills.length === 0 && (
              <div className="px-3 py-4 text-xs text-center" style={{ color: 'var(--text-faint)' }}>
                暂无已安装技能，点击下方管理
              </div>
            )}
            {skills.map((skill) => (
              <div
                key={skill.id}
                className="flex items-center gap-2 px-3 py-2 transition-colors"
                style={{ cursor: 'pointer' }}
                onClick={() => toggleSkill(skill)}
              >
                {/* 开关 */}
                <div
                  className="w-8 h-4 rounded-full relative flex-shrink-0 transition-colors"
                  style={{
                    background: skill.enabled ? 'var(--accent)' : 'var(--bg-panel)',
                    border: '1px solid var(--border-soft)',
                    opacity: busyId === skill.id ? 0.5 : 1,
                  }}
                >
                  <div
                    className="absolute top-1/2 -translate-y-1/2 w-3 h-3 rounded-full transition-all"
                    style={{
                      background: '#fff',
                      left: skill.enabled ? '16px' : '2px',
                      boxShadow: '0 1px 2px rgba(0,0,0,0.2)',
                    }}
                  />
                </div>

                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium truncate" style={{ color: skill.enabled ? 'var(--text)' : 'var(--text-faint)' }}>
                    {skill.name || skill.id}
                  </div>
                  {skill.description && (
                    <div className="text-[10px] truncate" style={{ color: 'var(--text-faint)' }} title={skill.description}>
                      {skill.description}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>

          <div style={{ borderTop: '1px solid var(--border-soft)' }}>
            <button
              className="w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors"
              style={{ color: 'var(--accent)' }}
              onClick={() => {
                setOpen(false)
                onManage?.()
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
              管理技能
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default SkillSelector
