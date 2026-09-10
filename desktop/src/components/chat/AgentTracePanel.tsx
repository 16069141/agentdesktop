import { useEffect, useMemo, useRef, useState } from 'react'

/**
 * Agent 深度思考轨迹可视化面板
 *
 * 消费 SSE 推送的 think / tool_action 事件，流式增量渲染 Agent 执行链路。
 * 定位：调试 Agent 工具调用、回调链路、执行状态问题。
 *
 * 协议：
 *   { event_type: 'think',       content: '推理文本' }
 *   { event_type: 'tool_action', content: '动作描述', status: 'pending'|'running'|'done' }
 *
 * 状态流转：带 merge:true 的事件表示「更新上一条动作的状态」，不新增行
 *   （同一动作 pending → running → done 只占一行，图标原地变化）。
 *
 * 配色全部走项目 CSS 变量（--bg-panel / --text-dim / --accent …），
 * 集成后自动跟随主题，无需改动。
 */

export type TraceEventType = 'think' | 'tool_action'
export type ActionStatus = 'pending' | 'running' | 'done'

export interface TraceEvent {
  event_type: TraceEventType
  content: string
  /** 仅 tool_action 需要 */
  status?: ActionStatus
  /** true = 更新上一条动作的状态，而非新增一行 */
  merge?: boolean
}

interface ThinkItem {
  kind: 'think'
  content: string
}

interface ActionItem {
  kind: 'action'
  status: ActionStatus
  content: string
}

type RenderItem = ThinkItem | ActionItem

const STATUS_ICON: Record<ActionStatus, string> = {
  pending: '🔘',
  running: '🔄',
  done: '✅',
}

/** 动作运行超过该秒数则显示「⏱ Ns」，避免长时间 running 看起来像卡死 */
const SLOW_THRESHOLD_SEC = 8

/** 把原始事件流标准化为渲染列表（merge 事件就地更新上一条动作） */
function normalize(events: TraceEvent[]): RenderItem[] {
  const items: RenderItem[] = []
  for (const e of events) {
    if (e.event_type === 'think') {
      items.push({ kind: 'think', content: e.content })
      continue
    }
    if (e.event_type === 'tool_action') {
      const last = items[items.length - 1]
      if (e.merge && last && last.kind === 'action') {
        if (e.status) last.status = e.status
        if (e.content) last.content = e.content
      } else {
        items.push({ kind: 'action', status: e.status ?? 'pending', content: e.content })
      }
    }
  }
  return items
}

/**
 * 适配器：把现有 SSE 事件（thinking / tool_call / tool_result）
 * 转换为本面板的 think / tool_action 协议，便于平滑迁移现有 ChatView 时间线。
 *
 * 映射关系：
 *   thinking    → think（推理文本）
 *   tool_call   → tool_action(running)  新增一行
 *   tool_result → tool_action(done)     merge 更新上一条，原地收束为 ✅
 *
 * @returns 转换后的事件；无法识别的类型返回 null（调用方忽略即可）
 */
export function fromLegacySSE(evt: {
  type?: string
  name?: string
  arguments?: string
  result?: string
  is_error?: boolean
  isError?: boolean
  content?: string
  chunk?: string
}): TraceEvent | null {
  const type = evt.type
  if (type === 'thinking') {
    return { event_type: 'think', content: evt.content ?? evt.chunk ?? '' }
  }
  if (type === 'tool_call') {
    return {
      event_type: 'tool_action',
      status: 'running',
      content: `调用工具：${evt.name ?? 'unknown'}`,
    }
  }
  if (type === 'tool_result') {
    const failed = Boolean(evt.is_error ?? evt.isError)
    return {
      event_type: 'tool_action',
      status: 'done',
      content: failed
        ? `${evt.name ?? 'unknown'} 执行失败`
        : `${evt.name ?? 'unknown'} 已返回`,
      merge: true,
    }
  }
  return null
}

/**
 * 适配器：把已处理的时间线条目（TraceItem[]）转换为本面板协议。
 * 用于直接替换 MessageItem 现有时间线（无需改动后端协议）。
 *
 * 映射：
 *   thinking    → think（正文取 detail，回退 label）
 *   tool_call   → tool_action(running)        新增行
 *   tool_result → tool_action(done) + merge   原地收束为 ✅
 *   error       → think（⚠️ 前缀，按文本段呈现）
 *   citation    → 跳过（面板外单独渲染「引用来源」区块）
 */
export function fromTraceItems(
  items: Array<{ kind?: string; label?: string; detail?: string; isError?: boolean }> | undefined
): TraceEvent[] {
  if (!items || items.length === 0) return []
  const out: TraceEvent[] = []
  for (const it of items) {
    const label = it.label ?? ''
    switch (it.kind) {
      case 'thinking':
        out.push({ event_type: 'think', content: it.detail || label || '思考中' })
        break
      case 'tool_call':
        out.push({
          event_type: 'tool_action',
          status: 'running',
          content: label.replace(/^调用工具：/, ''),
        })
        break
      case 'tool_result':
        out.push({
          event_type: 'tool_action',
          status: 'done',
          content: label.replace(/^工具返回：/, '') + (it.isError ? '（失败）' : ''),
          merge: true,
        })
        break
      case 'error':
        out.push({
          event_type: 'think',
          content: `⚠️ ${label}${it.detail ? `\n${it.detail}` : ''}`,
        })
        break
      default:
        break // citation 等交由外部渲染
    }
  }
  return out
}

export interface AgentTracePanelProps {
  /** SSE 增量累积的事件数组（新事件 push 进来即可触发重渲染） */
  events: TraceEvent[]
  title?: string
  defaultCollapsed?: boolean
  /** 内容区最大高度（px），超出滚动；默认 460 */
  maxHeight?: number
  /** 是否显示「回到底部」浮动按钮；默认 true */
  showToBottom?: boolean
}

export default function AgentTracePanel({
  events,
  title = 'Agent 执行轨迹',
  defaultCollapsed = false,
  maxHeight = 460,
  showToBottom = true,
}: AgentTracePanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)
  const bodyRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)

  /** 每条 running 动作的起始时刻（idx → ms），用于展示已运行时长 */
  const runningSinceRef = useRef<Map<number, number>>(new Map())
  const [, forceTick] = useState(0)

  const items = useMemo(() => normalize(events), [events])
  const thinkCount = items.filter((i) => i.kind === 'think').length
  const actionCount = items.filter((i) => i.kind === 'action').length
  const running = items.some((i) => i.kind === 'action' && i.status === 'running')

  /** 维护 running 起始时刻：新增 running 记下 now，转 done 后清除 */
  {
    const now = Date.now()
    items.forEach((item, idx) => {
      if (item.kind === 'action' && item.status === 'running') {
        if (!runningSinceRef.current.has(idx)) runningSinceRef.current.set(idx, now)
      } else {
        runningSinceRef.current.delete(idx)
      }
    })
  }

  /** 有 running 动作时每秒重渲染，驱动「已运行 Ns」计时 */
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => forceTick((v) => v + 1), 1000)
    return () => clearInterval(timer)
  }, [running])

  /** 自动滚动：仅在用户贴底时跟随，向上翻看历史时不打断 */
  useEffect(() => {
    if (collapsed || !atBottom) return
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [items.length, collapsed, atBottom])

  const onScroll = () => {
    const el = bodyRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    setAtBottom(near)
  }

  const scrollToBottom = () => {
    const el = bodyRef.current
    if (el) {
      el.scrollTop = el.scrollHeight
      setAtBottom(true)
    }
  }

  return (
    <div
      style={{
        background: 'var(--bg-panel)',
        border: '1px solid var(--border-soft)',
        borderRadius: 10,
        overflow: 'hidden',
      }}
    >
      {/* ── 标题栏 ── */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 13px',
          background: 'var(--bg-elev)',
          borderBottom: collapsed ? 'none' : '1px solid var(--border-soft)',
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 14 }}>🧠</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>{title}</span>

        {/* 运行中脉冲指示 */}
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: running ? 'var(--accent)' : 'var(--text-faint)',
            animation: running ? 'pulse 1.6s ease-out infinite' : undefined,
          }}
        />

        <span style={{ fontSize: 11, color: 'var(--text-faint)' }}>
          {thinkCount} 思考 <span style={{ opacity: 0.5, margin: '0 4px' }}>·</span> {actionCount} 动作
        </span>

        <button
          onClick={() => setCollapsed((v) => !v)}
          style={{
            marginLeft: 'auto',
            background: 'none',
            border: 'none',
            font: 'inherit',
            fontSize: 11,
            color: 'var(--text-faint)',
            cursor: 'pointer',
            padding: '4px 6px',
            borderRadius: 6,
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
          title={collapsed ? '展开' : '收起'}
        >
          {collapsed ? '展开' : '收起'}
          <span
            style={{
              display: 'inline-block',
              fontSize: 9,
              transform: collapsed ? 'rotate(180deg)' : 'none',
              transition: 'transform .2s ease',
            }}
          >
            ▲
          </span>
        </button>
      </div>

      {/* ── 内容区 ── */}
      {!collapsed && (
        <div
          ref={bodyRef}
          onScroll={onScroll}
          style={{
            maxHeight,
            overflowY: 'auto',
            padding: '12px 14px 14px',
            overscrollBehavior: 'contain',
          }}
        >
          {items.length === 0 ? (
            <div
              style={{
                textAlign: 'center',
                color: 'var(--text-faint)',
                fontSize: 12,
                padding: '36px 0',
              }}
            >
              <span style={{ fontSize: 22, display: 'block', marginBottom: 8, opacity: 0.55 }}>🧠</span>
              等待 Agent 执行事件…
            </div>
          ) : (
            items.map((item, idx) =>
              item.kind === 'think' ? (
                /* ── 深度思考：标题 + 正文段落 ── */
                <div key={idx} style={{ marginBottom: 12 }}>
                  <div
                    style={{
                      display: 'inline-block',
                      fontSize: 11,
                      fontWeight: 600,
                      color: 'var(--accent)',
                      background: 'var(--accent-soft, rgba(63,216,190,.12))',
                      padding: '2px 7px',
                      borderRadius: 5,
                      marginBottom: 5,
                    }}
                  >
                    【深度思考】
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      lineHeight: 1.75,
                      color: 'var(--text)',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      background: 'var(--bg-elev)',
                      borderLeft: '2.5px solid var(--accent)',
                      borderRadius: '0 6px 6px 0',
                      padding: '9px 12px',
                    }}
                  >
                    {item.content}
                  </div>
                </div>
              ) : (
                /* ── 工具动作：状态图标 + 浅灰小字 + 缩进 ── */
                <div
                  key={idx}
                  style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    gap: 7,
                    padding: '4px 0 4px 6px',
                    fontSize: 12,
                    lineHeight: 1.6,
                  }}
                >
                  <span
                    style={{
                      flexShrink: 0,
                      width: 14,
                      textAlign: 'center',
                      fontSize: 11,
                      animation: item.status === 'running' ? 'spin 1.1s linear infinite' : undefined,
                    }}
                  >
                    {STATUS_ICON[item.status]}
                  </span>
                  <span
                    style={{
                      minWidth: 0,
                      wordBreak: 'break-word',
                      color:
                        item.status === 'running'
                          ? 'var(--text-dim)'
                          : item.status === 'done'
                            ? 'var(--text-faint)'
                            : 'var(--text-faint)',
                      opacity: item.status === 'pending' ? 0.62 : 1,
                    }}
                  >
                    {item.content}
                    {item.status === 'running' &&
                      (() => {
                        const since = runningSinceRef.current.get(idx)
                        if (since == null) return null
                        const elapsed = Math.floor((Date.now() - since) / 1000)
                        return elapsed >= SLOW_THRESHOLD_SEC ? (
                          <span
                            style={{
                              marginLeft: 6,
                              fontSize: 10,
                              color: 'var(--warning, #d97706)',
                            }}
                            title={`已运行 ${elapsed}s，超过 ${SLOW_THRESHOLD_SEC}s 仍未返回`}
                          >
                            ⏱ {elapsed}s
                          </span>
                        ) : null
                      })()}
                  </span>
                </div>
              )
            )
          )}

          {/* 回到底部 */}
          {showToBottom && items.length > 0 && !atBottom && (
            <button
              onClick={scrollToBottom}
              style={{
                position: 'sticky',
                bottom: 0,
                display: 'block',
                margin: '0 auto',
                padding: '3px 10px',
                fontSize: 10,
                borderRadius: 20,
                background: 'rgba(31,41,55,.86)',
                color: '#fff',
                border: 'none',
                fontFamily: 'inherit',
                cursor: 'pointer',
              }}
            >
              ↓ 回到底部
            </button>
          )}
        </div>
      )}
    </div>
  )
}
