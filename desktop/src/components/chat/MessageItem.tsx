import React, { useState } from 'react'
import type { Message, TraceItem, Citation } from '../../types'

interface MessageItemProps {
  message: Message
  isSelected: boolean
  onSelect: (id: string | null) => void
  /** 事件时间线（思考 / 工具调用 / 工具结果 / 错误） */
  trace?: TraceItem[]
  /** 引用来源 */
  citations?: Citation[]
  /** 是否正在流式生成中 */
  streaming?: boolean
}

const ROLE_LABEL: Record<Message['role'], string> = {
  user: '你',
  assistant: 'AI',
  tool: '工具',
  system: '系统',
}

const TRACE_ICON: Record<TraceItem['kind'], string> = {
  thinking: '💭',
  tool_call: '🔧',
  tool_result: '📦',
  citation: '📚',
  error: '⚠',
}

const TRACE_COLOR: Record<TraceItem['kind'], string> = {
  thinking: 'var(--thinking)',
  tool_call: 'var(--tool)',
  tool_result: 'var(--ok)',
  citation: 'var(--thinking)',
  error: 'var(--danger)',
}

const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isSelected,
  onSelect,
  trace,
  citations,
  streaming = false,
}) => {
  const [showTimeline, setShowTimeline] = useState(false)
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const isSystem = message.role === 'system'

  const traceCount = trace?.length ?? 0
  const citationCount = citations?.length ?? 0
  const hasTimeline = traceCount > 0 || citationCount > 0

  return (
    <div
      className="mb-4 flex"
      style={{ justifyContent: isUser ? 'flex-end' : 'flex-start' }}
    >
      <div style={{ maxWidth: '80%', minWidth: hasTimeline ? '320px' : undefined }}>
        <div
          className="relative group rounded-xl p-3 cursor-pointer transition-shadow"
          style={{
            background: isUser
              ? 'var(--accent-soft)'
              : isSystem
                ? 'rgba(242,100,124,0.10)'
                : 'var(--bg-panel)',
            border: '1px solid',
            borderColor: isUser
              ? 'var(--accent)'
              : isSystem
                ? 'var(--danger)'
                : 'var(--border-soft)',
            color: 'var(--text)',
            boxShadow: isSelected ? '0 0 0 2px var(--accent)' : 'none',
          }}
          onClick={() => onSelect(isSelected ? null : message.id)}
        >
          {/* 角色标签 */}
          <div className="flex items-center gap-2 mb-1.5">
            <span
              className="px-1.5 py-0.5 rounded text-xs font-medium"
              style={{
                background: isUser ? 'var(--accent)' : isSystem ? 'var(--danger)' : 'var(--tool)',
                color: isUser ? 'var(--accent-ink)' : '#fff',
              }}
            >
              {ROLE_LABEL[message.role]}
            </span>
            {message.modelId && isAssistant && (
              <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
                {message.modelId}
              </span>
            )}
          </div>

          {/* 正文 */}
          <div
            className="text-sm whitespace-pre-wrap break-words leading-relaxed"
            style={{ fontFamily: 'var(--sans)' }}
          >
            {message.content}
            {streaming && (
              <span
                className="inline-block w-1.5 h-4 ml-0.5 align-middle"
                style={{ background: 'var(--accent)', animation: 'blink 1s step-end infinite' }}
              />
            )}
          </div>

          {/* 底部：时间 / 时间线入口 */}
          <div
            className="mt-1.5 flex items-center gap-2 text-xs"
            style={{ color: 'var(--text-faint)' }}
          >
            <span>{new Date(message.createdAt).toLocaleTimeString()}</span>
            {isAssistant && hasTimeline && (
              <button
                className="px-1.5 py-0.5 rounded transition-opacity"
                style={{ background: 'var(--bg-elev)', color: 'var(--text-dim)' }}
                onClick={(e) => {
                  e.stopPropagation()
                  setShowTimeline((v) => !v)
                }}
              >
                {showTimeline ? '收起' : `事件时间线 (${traceCount + citationCount})`}
              </button>
            )}
          </div>
        </div>

        {/* 事件时间线 */}
        {showTimeline && isAssistant && (
          <div
            className="mt-2 ml-2 p-3 rounded-lg text-xs"
            style={{
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-soft)',
            }}
          >
            <div className="font-medium mb-2" style={{ color: 'var(--text)' }}>
              事件时间线
            </div>
            <div className="space-y-2">
              {trace?.map((item) => (
                <div key={item.id} className="flex gap-2">
                  <span style={{ color: TRACE_COLOR[item.kind] }}>{TRACE_ICON[item.kind]}</span>
                  <div className="min-w-0 flex-1">
                    <div
                      style={{ color: item.isError ? 'var(--danger)' : 'var(--text-dim)' }}
                      className="font-medium"
                    >
                      {item.label}
                    </div>
                    {item.detail && (
                      <pre
                        className="mt-1 p-2 rounded overflow-x-auto whitespace-pre-wrap break-words"
                        style={{
                          background: 'var(--code-bg)',
                          color: 'var(--text-faint)',
                          fontFamily: 'var(--mono)',
                          fontSize: '11px',
                        }}
                      >
                        {item.detail.length > 800
                          ? `${item.detail.slice(0, 800)}…（已截断）`
                          : item.detail}
                      </pre>
                    )}
                  </div>
                </div>
              ))}

              {citationCount > 0 && (
                <div className="flex gap-2">
                  <span style={{ color: 'var(--thinking)' }}>📚</span>
                  <div className="flex-1">
                    <div className="font-medium" style={{ color: 'var(--text-dim)' }}>
                      引用来源 ({citationCount})
                    </div>
                    <div className="mt-1 space-y-1">
                      {citations?.map((c, i) => (
                        <div
                          key={`${c.docId}-${c.chunkIndex ?? i}`}
                          className="p-1.5 rounded"
                          style={{ background: 'var(--code-bg)' }}
                        >
                          <span style={{ color: 'var(--text-dim)' }}>{c.docName}</span>
                          {c.score != null && (
                            <span className="ml-1" style={{ color: 'var(--text-faint)' }}>
                              · 相关度 {c.score.toFixed(2)}
                            </span>
                          )}
                          {c.snippet && (
                            <div
                              className="mt-0.5 line-clamp-2"
                              style={{ color: 'var(--text-faint)' }}
                            >
                              {c.snippet}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default MessageItem
