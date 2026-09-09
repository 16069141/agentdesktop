import React, { useState, useEffect, useRef, useMemo } from 'react'
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
  const [copied, setCopied] = useState(false)
  const [hasSelection, setHasSelection] = useState(false)
  /** 执行过程面板中当前展开详情的步骤 id（null = 全部折叠） */
  const [expandedId, setExpandedId] = useState<string | null>(null)
  /** 步骤详情复制反馈 */
  const [copiedDetailId, setCopiedDetailId] = useState<string | null>(null)
  const bubbleRef = useRef<HTMLDivElement>(null)
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const isSystem = message.role === 'system'

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

  /** 工具名 → 中文动作 + 图标（豆包式步骤卡） */
  const TOOL_STEP: Record<string, { icon: string; action: string }> = {
    filesystem: { icon: '📁', action: '正在读取文件' },
    shell: { icon: '⚙️', action: '正在执行命令' },
    knowledge: { icon: '📚', action: '正在检索知识库' },
    code: { icon: '💻', action: '正在分析代码' },
    browser: { icon: '🌐', action: '正在浏览网页' },
    db_query: { icon: '🗄️', action: '正在查询数据库' },
    doc_to_html: { icon: '📄', action: '正在转换文档为 HTML' },
    rpa: { icon: '🤖', action: '正在执行自动化操作' },
  }
  const defaultStep = { icon: '🔧', action: '正在执行' }

  /** 从工具参数 JSON 提取简短摘要 */
  const toolSummary = (argsRaw: string | undefined): string => {
    if (!argsRaw) return ''
    try {
      const args = JSON.parse(argsRaw)
      const pick = (key: string) => {
        const v = args[key]
        return typeof v === 'string' ? v : v != null ? JSON.stringify(v) : ''
      }
      const candidate = pick('path') || pick('command') || pick('query') || pick('url') || pick('pattern') || pick('action')
      if (candidate) return candidate.length > 60 ? `${candidate.slice(0, 60)}…` : candidate
    } catch {
      /* 非 JSON 则原样 */
    }
    return argsRaw.length > 60 ? `${argsRaw.slice(0, 60)}…` : argsRaw
  }

  /** 判断某条 tool_call 是否仍"进行中"：
   *  按工具名配对后续 tool_result（支持同一回合并行多次调用），
   *  而不是只看相邻下一条 —— 并行调用时多条 tool_call 连续出现，
   *  旧逻辑会把已完成步骤误判为进行中 */
  const isStepPending = (index: number): boolean => {
    const item = trace?.[index]
    if (!item || item.kind !== 'tool_call') return false
    const toolName = item.label.replace('调用工具：', '')
    const rest = (trace || []).slice(index + 1)
    return !rest.some(
      (t) => t.kind === 'tool_result' && t.label.replace('工具返回：', '') === toolName
    )
  }

  /** 详情文本：JSON 可解析则美化排版，否则原样 */
  const prettyDetail = (raw: string | undefined): string => {
    if (!raw) return ''
    try {
      return JSON.stringify(JSON.parse(raw), null, 2)
    } catch {
      return raw
    }
  }

  /** 复制步骤详情 */
  const copyDetail = async (id: string, text: string) => {
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
    setCopiedDetailId(id)
    setTimeout(() => setCopiedDetailId((cur) => (cur === id ? null : cur)), 1500)
  }

  /** 步骤卡点击：展开 / 收起详情 */
  const toggleDetail = (id: string) => {
    setExpandedId((cur) => (cur === id ? null : id))
  }

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
      className="mb-4 flex"
      style={{ justifyContent: isUser ? 'flex-end' : 'flex-start' }}
    >
      <div style={{ maxWidth: '80%', minWidth: hasTimeline ? '320px' : undefined }}>
        <div
          ref={bubbleRef}
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
            userSelect: 'text',
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
                      onClick={() => window.electronAPI?.openFile(filePath)}
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
                      onClick={() => window.electronAPI?.revealInFolder(filePath)}
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
        </div>

        {/* 事件时间线（豆包式：思考/执行步骤实时展示） */}
        {showTimeline && isAssistant && (
          <div
            className="mt-2 ml-2 p-3 rounded-lg text-xs"
            style={{
              background: 'var(--bg-panel)',
              border: '1px solid var(--border-soft)',
            }}
          >
            <div className="font-medium mb-2 flex items-center gap-1.5" style={{ color: 'var(--text)' }}>
              执行过程
              {streaming && (
                <span className="text-[10px] font-normal flex items-center gap-1" style={{ color: 'var(--text-faint)' }}>
                  <span
                    className="inline-block w-2 h-2 rounded-full"
                    style={{ background: 'var(--accent)', animation: 'pulse 1s ease-in-out infinite' }}
                  />
                  进行中…
                </span>
              )}
            </div>
            <div className="space-y-1.5">
              {mergedTrace?.map((item, idx) => {
                // ── 思考：蓝色卡片，流式追加文本，点击展开完整内容 ──
                if (item.kind === 'thinking') {
                  const expanded = expandedId === item.id
                  const hasDetail = !!item.detail
                  return (
                    <div
                      key={item.id}
                      className="flex gap-2 items-start px-2 py-1.5 rounded-lg transition-colors"
                      style={{
                        background: 'var(--bg-elev)',
                        border: '1px solid var(--border-soft)',
                        cursor: hasDetail ? 'pointer' : 'default',
                      }}
                      onClick={hasDetail ? () => toggleDetail(item.id) : undefined}
                      title={hasDetail ? (expanded ? '收起' : '点击查看完整思考内容') : undefined}
                    >
                      <span
                        className="mt-1.5 inline-block w-2.5 h-2.5 rounded-full"
                        style={{ background: 'var(--thinking)', animation: 'pulse 1.4s ease-in-out infinite' }}
                      />
                      <div className="min-w-0 flex-1">
                        <div className="font-medium" style={{ color: 'var(--text-dim)' }}>
                          🤔 正在思考
                          {hasDetail && (
                            <span className="ml-1 text-[10px]" style={{ color: 'var(--text-faint)' }}>
                              {expanded ? '▲ 收起' : '▼ 展开'}
                            </span>
                          )}
                        </div>
                        {hasDetail && (
                          <div
                            className="mt-0.5 leading-relaxed whitespace-pre-wrap break-words"
                            style={{ color: 'var(--text-faint)', fontSize: '11px' }}
                          >
                            {expanded ? item.detail : item.detail!.length > 120 ? `${item.detail!.slice(0, 120)}…` : item.detail}
                          </div>
                        )}
                        {expanded && (
                          <div className="mt-1.5 flex items-center gap-2">
                            <button
                              className="px-1.5 py-0.5 rounded text-[10px] flex items-center gap-1"
                              style={{ background: 'var(--bg-panel)', color: 'var(--text-dim)', border: '1px solid var(--border-soft)', cursor: 'pointer' }}
                              onClick={(e) => { e.stopPropagation(); copyDetail(item.id, item.detail || '') }}
                            >
                              {copiedDetailId === item.id ? '✓ 已复制' : '复制内容'}
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                }

                // ── 工具调用：进行中 spinner / 已完成 ✓，点击展开完整参数 ──
                if (item.kind === 'tool_call') {
                  const step = TOOL_STEP[item.label.replace('调用工具：', '')] || defaultStep
                  const done = !isStepPending(idx)
                  const expanded = expandedId === item.id
                  const hasDetail = !!item.detail
                  return (
                    <div
                      key={item.id}
                      className="flex gap-2 items-start px-2 py-1.5 rounded-lg transition-colors"
                      style={{
                        background: 'var(--bg-elev)',
                        border: '1px solid var(--border-soft)',
                        cursor: hasDetail ? 'pointer' : 'default',
                      }}
                      onClick={hasDetail ? () => toggleDetail(item.id) : undefined}
                      title={hasDetail ? (expanded ? '收起' : '点击查看完整参数') : undefined}
                    >
                      {done ? (
                        <span className="mt-0.5 flex-shrink-0 w-4 h-4 rounded-full flex items-center justify-center" style={{ background: '#22c55e', color: '#fff' }}>
                          <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="20 6 9 17 4 12" />
                          </svg>
                        </span>
                      ) : (
                        <span
                          className="mt-0.5 flex-shrink-0 w-4 h-4 rounded-full"
                          style={{
                            border: '2px solid var(--border-soft)',
                            borderTopColor: 'var(--accent)',
                            animation: 'spin 0.8s linear infinite',
                          }}
                        />
                      )}
                      <div className="min-w-0 flex-1">
                        <div className="font-medium flex items-center gap-1.5" style={{ color: done ? 'var(--text-dim)' : 'var(--text)' }}>
                          <span>{step.icon}</span>
                          <span>{done ? step.action.replace('正在', '已') : step.action}</span>
                          {hasDetail && (
                            <span className="ml-auto text-[10px] flex-shrink-0" style={{ color: 'var(--text-faint)' }}>
                              {expanded ? '▲ 收起' : '▼ 展开'}
                            </span>
                          )}
                        </div>
                        {!expanded && item.detail && (
                          <div
                            className="mt-0.5 font-mono truncate"
                            style={{ color: 'var(--text-faint)', fontSize: '10px' }}
                            title={item.detail}
                          >
                            {toolSummary(item.detail)}
                          </div>
                        )}
                        {expanded && hasDetail && (
                          <div className="mt-1.5">
                            <pre
                              className="p-2 rounded overflow-x-auto whitespace-pre-wrap break-words"
                              style={{ background: 'var(--code-bg)', color: 'var(--text-dim)', fontFamily: 'var(--mono)', fontSize: '10px', maxHeight: '240px', overflowY: 'auto' }}
                            >
                              {prettyDetail(item.detail)}
                            </pre>
                            <div className="mt-1.5 flex items-center gap-2">
                              <button
                                className="px-1.5 py-0.5 rounded text-[10px] flex items-center gap-1"
                                style={{ background: 'var(--bg-panel)', color: 'var(--text-dim)', border: '1px solid var(--border-soft)', cursor: 'pointer' }}
                                onClick={(e) => { e.stopPropagation(); copyDetail(item.id, item.detail || '') }}
                              >
                                {copiedDetailId === item.id ? '✓ 已复制' : '复制参数'}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                }

                // ── 工具结果：完成摘要（成功/失败），点击展开完整内容 ──
                if (item.kind === 'tool_result') {
                  const name = item.label.replace('工具返回：', '')
                  const step = TOOL_STEP[name] || defaultStep
                  const expanded = expandedId === item.id
                  const hasDetail = !!item.detail
                  return (
                    <div
                      key={item.id}
                      className="flex gap-2 items-start px-2 py-1.5 rounded-lg transition-colors"
                      style={{
                        background: 'var(--bg-elev)',
                        border: '1px solid var(--border-soft)',
                        cursor: hasDetail ? 'pointer' : 'default',
                      }}
                      onClick={hasDetail ? () => toggleDetail(item.id) : undefined}
                      title={hasDetail ? (expanded ? '收起' : '点击查看完整结果') : undefined}
                    >
                      <span className="mt-0.5 flex-shrink-0" style={{ color: item.isError ? 'var(--danger)' : '#22c55e' }}>
                        {item.isError ? '✕' : '✓'}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="font-medium flex items-center gap-1.5" style={{ color: item.isError ? 'var(--danger)' : '#4ade80' }}>
                          <span>{step.icon}</span>
                          <span>{name === 'filesystem' ? '文件操作' : name === 'shell' ? '命令执行' : name} {item.isError ? '失败' : '完成'}</span>
                          {hasDetail && (
                            <span className="ml-auto text-[10px] flex-shrink-0" style={{ color: 'var(--text-faint)' }}>
                              {expanded ? '▲ 收起' : '▼ 展开'}
                            </span>
                          )}
                        </div>
                        {!expanded && item.detail && (
                          <div
                            className="mt-0.5 leading-relaxed line-clamp-2"
                            style={{ color: 'var(--text-faint)', fontSize: '10px' }}
                          >
                            {item.detail.length > 200 ? `${item.detail.slice(0, 200)}…` : item.detail}
                          </div>
                        )}
                        {expanded && hasDetail && (
                          <div className="mt-1.5">
                            <pre
                              className="p-2 rounded overflow-x-auto whitespace-pre-wrap break-words"
                              style={{ background: 'var(--code-bg)', color: 'var(--text-dim)', fontFamily: 'var(--mono)', fontSize: '10px', maxHeight: '300px', overflowY: 'auto' }}
                            >
                              {prettyDetail(item.detail)}
                            </pre>
                            <div className="mt-1.5 flex items-center gap-2">
                              <button
                                className="px-1.5 py-0.5 rounded text-[10px] flex items-center gap-1"
                                style={{ background: 'var(--bg-panel)', color: 'var(--text-dim)', border: '1px solid var(--border-soft)', cursor: 'pointer' }}
                                onClick={(e) => { e.stopPropagation(); copyDetail(item.id, item.detail || '') }}
                              >
                                {copiedDetailId === item.id ? '✓ 已复制' : '复制结果'}
                              </button>
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  )
                }

                // ── 其他（错误等） ──
                return (
                  <div key={item.id} className="flex gap-2">
                    <span style={{ color: TRACE_COLOR[item.kind] }}>{TRACE_ICON[item.kind]}</span>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium" style={{ color: item.isError ? 'var(--danger)' : 'var(--text-dim)' }}>
                        {item.label}
                      </div>
                      {item.detail && (
                        <pre
                          className="mt-1 p-2 rounded overflow-x-auto whitespace-pre-wrap break-words"
                          style={{ background: 'var(--code-bg)', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontSize: '11px' }}
                        >
                          {item.detail.length > 800 ? `${item.detail.slice(0, 800)}…（已截断）` : item.detail}
                        </pre>
                      )}
                    </div>
                  </div>
                )
              })}

              {/* 流式生成中：有步骤时显示「正在生成回答…」，无步骤时显示「正在思考」 */}
              {streaming && trace && trace.length > 0 && (
                <div className="flex gap-2 items-center" style={{ color: 'var(--text-faint)' }}>
                  <span className="inline-block w-1.5 h-3" style={{ background: 'var(--accent)', animation: 'blink 1s step-end infinite' }} />
                  <span>正在生成回答…</span>
                </div>
              )}
              {streaming && trace && trace.length === 0 && (
                <div className="flex gap-2 items-center" style={{ color: 'var(--text-faint)' }}>
                  <span
                    className="inline-block w-3 h-3 rounded-full"
                    style={{
                      border: '2px solid var(--border-soft)',
                      borderTopColor: 'var(--accent)',
                      animation: 'spin 0.8s linear infinite',
                    }}
                  />
                  <span>🤔 正在思考…</span>
                </div>
              )}

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
