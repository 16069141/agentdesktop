import React, { useEffect, useState, useCallback } from 'react'
import { api } from '../../api'

interface ScheduleJob {
  id: string
  title: string
  message: string
  model_id?: string
  schedule: Record<string, unknown>
  schedule_text: string
  enabled: boolean
  next_run_at: number | null
  last_run_at: number | null
  last_status: string
  created_at: number
  updated_at: number
}

interface NotificationItem {
  id: string
  job_id?: string
  title: string
  message: string
  status: string
  created_at: number
  read: number
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待运行', color: '#6b7280' },
  running: { label: '运行中', color: '#3b82f6' },
  done: { label: '已完成', color: '#10b981' },
  failed: { label: '失败', color: '#ef4444' },
  cancelled: { label: '已取消', color: '#f59e0b' },
}

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const ScheduleView: React.FC = () => {
  const [jobs, setJobs] = useState<ScheduleJob[]>([])
  const [notifs, setNotifs] = useState<NotificationItem[]>([])
  const [unread, setUnread] = useState(0)
  const [loading, setLoading] = useState(true)
  const [toast, setToast] = useState('')

  // 新建表单
  const [title, setTitle] = useState('')
  const [message, setMessage] = useState('')
  const [sType, setSType] = useState<'daily' | 'weekly' | 'interval' | 'hourly' | 'at'>('daily')
  const [sTime, setSTime] = useState('09:00')
  const [sWeekday, setSWeekday] = useState(0)
  const [sIntervalN, setSIntervalN] = useState(30)
  const [sIntervalUnit, setSIntervalUnit] = useState<'minutes' | 'hours' | 'days'>('minutes')
  const [sHourMin, setSHourMin] = useState('00')
  const [sAt, setSAt] = useState('now + 1 minute')

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(''), 3000)
  }

  const load = useCallback(async () => {
    try {
      const [s, n] = await Promise.all([api.schedule.list(), api.schedule.notifications()])
      setJobs(s.jobs || [])
      setNotifs(n.items || [])
      setUnread(n.unread ?? 0)
    } catch (e) {
      showToast(`加载失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = setInterval(() => {
      api.schedule.notifications().then((n) => {
        setNotifs(n.items || [])
        setUnread(n.unread ?? 0)
      }).catch(() => {})
    }, 20000)
    return () => clearInterval(timer)
  }, [load])

  const buildSchedule = (): Record<string, unknown> | null => {
    if (sType === 'daily') return { type: 'daily', value: sTime || '09:00' }
    if (sType === 'weekly') return { type: 'weekly', value: [WEEKDAYS[sWeekday], sTime || '09:00'] }
    if (sType === 'interval')
      return { type: 'interval', unit: sIntervalUnit, value: Math.max(1, sIntervalN) }
    if (sType === 'hourly') return { type: 'hourly', value: sHourMin || '00' }
    if (sType === 'at') return { type: 'at', value: sAt }
    return null
  }

  const handleCreate = async () => {
    if (!title.trim() || !message.trim()) {
      showToast('请填写任务名称和指令内容')
      return
    }
    const schedule = buildSchedule()
    if (!schedule) return
    try {
      const res = await api.schedule.create({
        title: title.trim(),
        message: message.trim(),
        schedule,
      })
      showToast(`已创建，下次运行：${new Date(res.next_run_at * 1000).toLocaleString()}`)
      setTitle('')
      setMessage('')
      void load()
    } catch (e) {
      showToast(`创建失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleToggle = async (job: ScheduleJob) => {
    try {
      await api.schedule.update(job.id, { enabled: !job.enabled })
      showToast(job.enabled ? '已停用（下次不再自动运行）' : '已启用')
      void load()
    } catch (e) {
      showToast(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleRunNow = async (job: ScheduleJob) => {
    try {
      await api.schedule.runNow(job.id)
      showToast('已触发运行，结果稍后出现在通知里')
      void load()
    } catch (e) {
      showToast(`触发失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleDelete = async (job: ScheduleJob) => {
    if (!window.confirm(`删除定时任务「${job.title}」？`)) return
    try {
      await api.schedule.remove(job.id)
      showToast('已删除')
      void load()
    } catch (e) {
      showToast(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const handleMarkRead = async () => {
    try {
      await api.schedule.markRead()
      setUnread(0)
      setNotifs((prev) => prev.map((n) => ({ ...n, read: 1 })))
    } catch (e) {
      showToast(`操作失败：${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const fmt = (ts: number | null) =>
    ts ? new Date(ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'

  return (
    <div className="max-w-3xl mx-auto px-6 py-6">
      <div className="flex items-center justify-between mb-1">
        <h2 className="text-lg font-semibold">定时任务（主动触发）</h2>
        {unread > 0 && (
          <span
            className="px-2 py-0.5 rounded-full text-[11px] font-bold"
            style={{ background: 'var(--danger)', color: '#fff' }}
          >
            {unread} 条新结果
          </span>
        )}
      </div>
      <p className="text-xs mb-4" style={{ color: 'var(--text-faint)' }}>
        Agent 会按计划在后台自动执行你的指令，完成后把结果送达到这里的通知。
        支持：每天 / 每周 / 每 N 分钟·小时·天 / 每小时第几分 / 一次性（now + N 分钟等）。
      </p>

      {toast && (
        <div className="mb-3 px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}>
          {toast}
        </div>
      )}

      {/* 通知 */}
      <div className="mb-5 rounded-xl px-4 py-3" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold" style={{ color: 'var(--text-dim)' }}>
            运行结果 {unread > 0 && <span style={{ color: 'var(--danger)' }}>（{unread} 未读）</span>}
          </div>
          {unread > 0 && (
            <button className="text-[11px]" style={{ color: 'var(--accent)' }} onClick={handleMarkRead}>
              全部已读
            </button>
          )}
        </div>
        {notifs.length === 0 ? (
          <div className="text-xs py-3 text-center" style={{ color: 'var(--text-faint)' }}>
            暂无运行结果。任务执行完成后会出现在这里。
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {notifs.slice(0, 8).map((n) => {
              const meta = STATUS_META[n.status] || { label: n.status, color: '#6b7280' }
              return (
                <div
                  key={n.id}
                  className="px-3 py-2 rounded-lg"
                  style={{
                    background: 'var(--bg)',
                    border: '1px solid var(--border-soft)',
                    opacity: n.read ? 0.65 : 1,
                  }}
                >
                  <div className="flex items-center gap-2 text-xs">
                    <span className="font-semibold" style={{ color: 'var(--text)' }}>{n.title}</span>
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ background: `${meta.color}22`, color: meta.color }}>
                      {meta.label}
                    </span>
                    {!n.read && <span className="text-[10px]" style={{ color: 'var(--danger)' }}>● 新</span>}
                    <span className="ml-auto text-[10px]" style={{ color: 'var(--text-faint)' }}>{fmt(n.created_at)}</span>
                  </div>
                  <div className="text-xs mt-1 break-words" style={{ color: 'var(--text-dim)' }}>{n.message}</div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 新建 */}
      <div className="mb-5 rounded-xl px-4 py-3" style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}>
        <div className="text-xs font-semibold mb-2" style={{ color: 'var(--text-dim)' }}>新建定时任务</div>
        <div className="flex gap-2 mb-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="任务名称，如：晨会提醒"
            className="w-40 px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          />
          <input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
            placeholder="Agent 要执行的指令，如：提醒我今天上午 10 点开晨会"
            className="flex-1 px-3 py-1.5 rounded-lg text-sm outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          />
        </div>
        <div className="flex gap-2 mb-2 flex-wrap items-center">
          <select
            value={sType}
            onChange={(e) => setSType(e.target.value as typeof sType)}
            className="px-2 py-1.5 rounded-lg text-xs outline-none"
            style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}
          >
            <option value="daily">每天</option>
            <option value="weekly">每周</option>
            <option value="interval">每 N 分钟/小时/天</option>
            <option value="hourly">每小时第 N 分</option>
            <option value="at">一次性</option>
          </select>

          {sType === 'daily' && (
            <input type="time" value={sTime} onChange={(e) => setSTime(e.target.value)}
              className="px-2 py-1.5 rounded-lg text-xs outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }} />
          )}
          {sType === 'weekly' && (
            <>
              <select value={sWeekday} onChange={(e) => setSWeekday(Number(e.target.value))}
                className="px-2 py-1.5 rounded-lg text-xs outline-none"
                style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}>
                {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
              </select>
              <input type="time" value={sTime} onChange={(e) => setSTime(e.target.value)}
                className="px-2 py-1.5 rounded-lg text-xs outline-none"
                style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }} />
            </>
          )}
          {sType === 'interval' && (
            <>
              <input type="number" min={1} value={sIntervalN} onChange={(e) => setSIntervalN(Number(e.target.value))}
                className="w-16 px-2 py-1.5 rounded-lg text-xs outline-none"
                style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }} />
              <select value={sIntervalUnit} onChange={(e) => setSIntervalUnit(e.target.value as typeof sIntervalUnit)}
                className="px-2 py-1.5 rounded-lg text-xs outline-none"
                style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }}>
                <option value="minutes">分钟</option>
                <option value="hours">小时</option>
                <option value="days">天</option>
              </select>
            </>
          )}
          {sType === 'hourly' && (
            <input type="number" min={0} max={59} value={sHourMin} onChange={(e) => setSHourMin(e.target.value)}
              className="w-16 px-2 py-1.5 rounded-lg text-xs outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }} />
          )}
          {sType === 'at' && (
            <input value={sAt} onChange={(e) => setSAt(e.target.value)} placeholder="now + 1 minute / tomorrow 09:15 / HH:MM"
              className="flex-1 min-w-40 px-3 py-1.5 rounded-lg text-xs outline-none"
              style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text)' }} />
          )}
          <button
            className="ml-auto px-3 py-1.5 rounded-lg text-xs font-medium"
            style={{ background: 'var(--accent)', color: 'var(--accent-ink)' }}
            onClick={handleCreate}
          >
            创建
          </button>
        </div>
      </div>

      {/* 任务列表 */}
      {loading ? (
        <div className="text-sm py-8 text-center" style={{ color: 'var(--text-faint)' }}>加载中…</div>
      ) : jobs.length === 0 ? (
        <div className="text-sm py-10 text-center" style={{ color: 'var(--text-faint)' }}>
          还没有定时任务。在上方创建第一个，Agent 就会开始按计划主动工作。
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {jobs.map((j) => {
            const meta = STATUS_META[j.last_status] || { label: j.last_status, color: '#6b7280' }
            return (
              <div key={j.id} className="px-4 py-3 rounded-xl"
                style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', opacity: j.enabled ? 1 : 0.55 }}>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>{j.title}</span>
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded" style={{ background: `${meta.color}22`, color: meta.color }}>
                    {meta.label}
                  </span>
                  <span className="text-[11px] ml-auto" style={{ color: 'var(--text-dim)' }}>{j.schedule_text}</span>
                </div>
                <div className="text-xs mt-1 break-words" style={{ color: 'var(--text-dim)' }}>{j.message}</div>
                <div className="flex items-center gap-3 mt-2">
                  <span className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
                    {j.enabled ? `下次：${fmt(j.next_run_at)}` : '已停用'}
                    {j.last_run_at ? ` · 上次：${fmt(j.last_run_at)}` : ''}
                  </span>
                  <div className="ml-auto flex items-center gap-1.5">
                    <button className="px-2 py-1 rounded-md text-[11px]" style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text-dim)' }}
                      onClick={() => handleRunNow(j)}>立即运行</button>
                    <button className="px-2 py-1 rounded-md text-[11px]" style={{ background: 'var(--bg)', border: '1px solid var(--border-soft)', color: 'var(--text-dim)' }}
                      onClick={() => handleToggle(j)}>{j.enabled ? '停用' : '启用'}</button>
                    <button className="px-2 py-1 rounded-md text-[11px]" style={{ background: 'var(--danger)', color: '#fff' }}
                      onClick={() => handleDelete(j)}>删除</button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default ScheduleView
