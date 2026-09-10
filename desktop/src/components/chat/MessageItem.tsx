import React, { useState, useEffect, useRef, useMemo } from 'react'
import type { Message, TraceItem, Citation } from '../../types'
import MarkdownContent from './MarkdownContent'
import AgentTracePanel, { fromTraceItems } from './AgentTracePanel'

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

const MessageItem: React.FC<MessageItemProps> = ({
  message,
  isSelected,
  onSelect,
  trace,
  citations,
  streaming = false,
}) => {
  const [showTimeline, setShowTimeline] = useState(false)
  const [copied, setCopied] = useState(false)
  const [hasSelection, setHasSelection] = useState(false)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const isSystem = message.role === 'system'

  /** 扁平布局下的角色色：用户用主题强调色，AI 用次级文字色，系统/工具用语义色 */
  const roleColor = isUser
    ? 'var(--accent)'
    : isSystem
      ? 'var(--danger)'
      : isAssistant
        ? 'var(--text-dim)'
        : 'var(--tool)'

  /** 从正文中提取绝对路径（兜底：模型直接在文本里输出交付路径）
   *  正则要求至少 2 级目录段（排除 `/html/xxx.html` 这类相对片段误匹配） */
  const textFilePaths = useMemo(() => {
    const re = /(?:\/(?:[\w\-. \u4e00-\u9fa5]+\/){2,}[\w\-. \u4e00-\u9fa5]+\.(?:html?|md|markdown|txt|docx?|xlsx?|pptx?|pdf|json|csv|png|jpe?g|gif|svg|zip|yaml|yml))/g
    const found = message.content?.match(re) || []
    return [...new Set(found.map((p) => p.trim()))]
  }, [message.content])

  /** 交付文件总列表：结构化 saved_files + 正文兜底，按文件名去重（同名只保留结构化完整路径） */
  const deliverFiles = useMemo(() => {
    const structured = message.generatedFiles || []
    const seen = new Set<string>()
    const out: string[] = []
    for (const p of [...structured, ...textFilePaths]) {
      const name = p.split('/').pop() || p
      if (seen.has(name)) continue
      seen.add(name)
      out.push(p)
    }
    return out
  }, [message.generatedFiles, textFilePaths])

  /** 连续 thinking 合并为一条（多个"正在思考"不重复展示，只保留最新内容） */
  const mergedTrace = useMemo(() => {
    if (!trace || trace.length === 0) return trace
    const out: typeof trace = []
    for (const item of trace) {
      const last = out[out.length - 1]
      if (item.kind === 'thinking' && last && last.kind === 'thinking') {
        if (item.detail) last.detail = item.detail
        continue
      }
      out.push(item)
    }
    return out
  }, [trace])

  const traceCount = mergedTrace?.length ?? 0
  const citationCount = citations?.length ?? 0
  const hasTimeline = traceCount > 0 || citationCount > 0

  /** 监听选中文本：是否在当前消息气泡内有选中 */
  useEffect(() => {
    const check = () => {
      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
        setHasSelection(false)
        return
      }
      const range = sel.getRangeAt(0)
      if (bubbleRef.current && bubbleRef.current.contains(range.commonAncestorContainer)) {
        setHasSelection(sel.toString().trim().length > 0)
      } else {
        setHasSelection(false)
      }
    }
    document.addEventListener('selectionchange', check)
    return () => document.removeEventListener('selectionchange', check)
  }, [])

  /** 复制：有选中文本时复制选中部分，否则复制全部 */
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    // 优先取选中文本
    let text = ''
    const sel = window.getSelection()
    if (sel && !sel.isCollapsed && sel.rangeCount > 0) {
      const range = sel.getRangeAt(0)
      if (bubbleRef.current && bubbleRef.current.contains(range.commonAncestorContainer)) {
        text = sel.toString()
      }
    }
    // 没有选中则复制整条消息
    if (!text.trim()) {
      text = message.content || ''
    }
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      try { document.execCommand('copy') } catch { /* ignore */ }
      document.body.removeChild(ta)
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div
      ref={bubbleRef}
      className="msg-row"
      style={{
        userSelect: 'text',
        padding: '16px 0',
        boxShadow: isSelected ? 'inset 3px 0 0 var(--accent)' : 'none',
      }}
      onClick={() => onSelect(isSelected ? null : message.id)}
    >
      {/* 头部：角色 / 模型 / 时间 —— 无边框、靠文字层级区分 */}
      <div className="flex items-center gap-2 mb-2 select-none">
        <span
          className="inline-block w-1.5 h-1.5 rounded-full flex-shrink-0"
          style={{ background: roleColor }}
        />
        <span className="text-xs font-medium tracking-wide" style={{ color: roleColor }}>
          {ROLE_LABEL[message.role]}
        </span>
        {message.modelId && isAssistant && (
          <span className="text-xs" style={{ color: 'var(--text-faint)' }}>
            {message.modelId}
          </span>
        )}
        <span className="text-xs ml-auto" style={{ color: 'var(--text-faint)' }}>
          {new Date(message.createdAt).toLocaleTimeString()}
        </span>
      </div>

          {/* 图片附件 */}
          {message.images && message.images.length > 0 && (
            <div className="flex gap-2 mb-2 flex-wrap">
              {message.images.map((img, idx) => (
                <img
                  key={idx}
                  src={img}
                  alt={`图片 ${idx + 1}`}
                  className="rounded-lg max-h-48 object-cover"
                  style={{ border: '1px solid var(--border-soft)', maxWidth: '100%' }}
                />
              ))}
            </div>
          )}

          {/* 文档附件 */}
          {message.attachments && message.attachments.length > 0 && (
            <div className="flex flex-col gap-1.5 mb-2">
              {message.attachments.map((att, idx) => {
                const kindColors: Record<string, string> = {
                  text: '#6b7280', pdf: '#ef4444', docx: '#3b82f6', xlsx: '#10b981', pptx: '#f59e0b',
                }
                const kindLabels: Record<string, string> = {
                  text: 'TXT', pdf: 'PDF', docx: 'DOC', xlsx: 'XLS', pptx: 'PPT',
                }
                const color = kindColors[att.kind] || '#6b7280'
                const label = kindLabels[att.kind] || 'FILE'
                return (
                  <div
                    key={idx}
                    className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg"
                    style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
                  >
                    <span
                      className="flex-shrink-0 w-7 h-7 rounded flex items-center justify-center text-[9px] font-bold"
                      style={{ background: `${color}22`, color }}
                    >
                      {label}
                    </span>
                    <span className="text-xs font-medium truncate flex-1" style={{ color: 'var(--text)' }} title={att.filename}>
                      {att.filename}
                    </span>
                    <span className="text-[10px] flex-shrink-0" style={{ color: 'var(--text-faint)' }}>
                      {att.char_count.toLocaleString()} 字符{att.truncated ? '（已截断）' : ''}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {/* 正文：AI/系统 → Markdown 结构化渲染（表格/代码/标题/引用等）；用户 → 原样保留换行 */}
          {isAssistant || isSystem ? (
            <MarkdownContent content={message.content || ''} />
          ) : (
            <div
              className="whitespace-pre-wrap break-words"
              style={{ fontSize: 16, lineHeight: 1.75, fontFamily: 'var(--sans)' }}
            >
              {message.content}
            </div>
          )}
          {streaming && (
            <span
              className="inline-block w-1.5 h-4 ml-0.5 align-middle"
              style={{ background: 'var(--accent)', animation: 'blink 1s step-end infinite' }}
            />
          )}

          {/* Agent 交付文件：打开文件 / 在 Finder 中显示 */}
          {isAssistant && deliverFiles.length > 0 && (
            <div className="mt-2 space-y-1.5">
              {deliverFiles.map((filePath) => {
                const fileName = filePath.split('/').pop() || filePath
                return (
                  <div
                    key={filePath}
                    className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg"
                    style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)' }}
                  >
                    <span
                      className="flex-shrink-0 w-7 h-7 rounded flex items-center justify-center text-xs font-bold"
                      style={{ background: '#22c55e22', color: '#22c55e' }}
                    >
                      📄
                    </span>
                    <span className="text-xs font-medium truncate flex-1" style={{ color: 'var(--text)' }} title={filePath}>
                      {fileName}
                    </span>
                    <button
                      className="px-2 py-1 rounded text-[11px] transition-colors flex-shrink-0"
                      style={{ background: 'var(--accent)', color: '#fff', border: 'none', cursor: 'pointer' }}
                      onClick={(e) => {
                        e.stopPropagation()
                        window.electronAPI?.openFile(filePath)
                      }}
                      title={`打开文件 ${filePath}`}
                    >
                      打开文件
                    </button>
                    <button
                      className="px-2 py-1 rounded text-[11px] transition-colors flex-shrink-0"
                      style={{
                        background: 'transparent',
                        color: 'var(--text-dim)',
                        border: '1px solid var(--border-soft)',
                        cursor: 'pointer',
                      }}
                      onClick={(e) => {
                        e.stopPropagation()
                        window.electronAPI?.revealInFolder(filePath)
                      }}
                      title={`在 Finder 中显示 ${filePath}`}
                    >
                      浏览文件夹
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {/* 底部：时间 / 复制 / 时间线入口 */}
          <div
            className="mt-1.5 flex items-center gap-2 text-xs"
            style={{ color: 'var(--text-faint)' }}
          >
            <span>{new Date(message.createdAt).toLocaleTimeString()}</span>
            <button
              className="px-1.5 py-0.5 rounded transition-colors flex items-center gap-1"
              style={{
                background: copied ? 'var(--ok)' : 'var(--bg-elev)',
                color: copied ? '#fff' : 'var(--text-dim)',
                border: 'none',
                cursor: 'pointer',
              }}
              onMouseDown={(e) => e.preventDefault()}
              onClick={handleCopy}
              title={hasSelection ? '复制选中文字' : '复制全部消息内容'}
            >
              {copied ? (
                <>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  已复制
                </>
              ) : (
                <>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  {hasSelection ? '复制选中' : '复制'}
                </>
              )}
            </button>
            {isAssistant && hasTimeline && (
              <button
                className="px-1.5 py-0.5 rounded transition-colors flex items-center gap-1"
                style={{
                  background: showTimeline ? 'var(--accent-soft)' : 'var(--bg-elev)',
                  color: showTimeline ? 'var(--accent)' : 'var(--text-dim)',
                  cursor: 'pointer',
                }}
                onClick={(e) => {
                  e.stopPropagation()
                  setShowTimeline((v) => !v)
                }}
                title={showTimeline ? '收起执行过程' : '展开执行过程'}
              >
                <svg
                  width="10"
                  height="10"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  style={{ transition: 'transform .15s ease', transform: showTimeline ? 'rotate(90deg)' : 'none' }}
                >
                  <polyline points="9 18 15 12 9 6" />
                </svg>
                {showTimeline ? '收起' : `执行过程 (${traceCount + citationCount})`}
              </button>
            )}
          </div>

        {/* 事件时间线（豆包式：思考/执行步骤实时展示） */}
        {showTimeline && isAssistant && (
          <div className="mt-2 ml-2 space-y-2">
            <AgentTracePanel
              events={fromTraceItems(mergedTrace ?? [])}
              title="执行过程"
              maxHeight={460}
            />

            {citationCount > 0 && (
            <div
              className="p-3 rounded-lg text-xs flex gap-2"
              style={{
                background: 'var(--bg-panel)',
                border: '1px solid var(--border-soft)',
              }}
            >
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
        )}
    </div>
  )
}

export default MessageItem
