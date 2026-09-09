import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useUiStore } from '../../store/useUiStore'
import { api } from '../../api'
import { streamChat } from '../../sse/sseClient'
import MessageItem from './MessageItem'
import InputBox from './InputBox'
import type { Message, TraceItem, Citation, FileAttachment } from '../../types'

const QUICK_COMMANDS = [
  { text: '查公司请假制度', icon: '📋' },
  { text: '运行 ls 命令', icon: '💻' },
  { text: '分析一段代码', icon: '🔍' },
  { text: '总结项目进展', icon: '📊' },
]

/** 为流式过程中产生的事件生成稳定 key */
let traceSeq = 0
const nextTraceId = () => `trace-${Date.now()}-${traceSeq++}`

// 时间线/引用按 messageId 累积，长时间使用会无限增长。
// 限制保留的消息条数：超过后丢弃最早消息的记录（仅影响历史消息的
// 过程折叠展示，消息正文与交付文件不受影响）。
const MAX_TRACE_MESSAGES = 60

function capRecord<T>(rec: Record<string, T>): Record<string, T> {
  const keys = Object.keys(rec)
  if (keys.length <= MAX_TRACE_MESSAGES) return rec
  const kept = keys.slice(keys.length - MAX_TRACE_MESSAGES)
  const next: Record<string, T> = {}
  for (const k of kept) next[k] = rec[k]
  return next
}

const ChatView: React.FC = () => {
  const {
    currentConversationId,
    messages,
    setMessages,
    appendMessage,
    setIsStreaming,
    streamingMessageId,
    selectedMessageId,
    setSelectedMessageId,
    currentModelId,
    models,
    setCurrentModelId,
    setActiveTab,
    conversations,
    setConversations,
    updateMessageInConversation,
    activeStreamControllers,
    streamingConversationIds,
    registerActiveStream,
    abortActiveStream,
    clearActiveStream,
    chatMode,
    setChatMode,
  } = useUiStore()

  /** 每条助理消息的事件时间线 */
  const [traces, setTraces] = useState<Record<string, TraceItem[]>>({})
  const [citations, setCitations] = useState<Record<string, Citation[]>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tokenHint, setTokenHint] = useState('剩余 14,200 / 16,384 tokens')

  const messagesEndRef = useRef<HTMLDivElement>(null)
  /** 各助理消息已收集的交付文件路径（跨 tool_result 事件累积去重） */
  const generatedFilesRef = useRef<Record<string, string[]>>({})

  const currentModel = models.find((m) => m.id === currentModelId)
  /** 当前对话是否正在流式输出 */
  const isCurrentStreaming = currentConversationId
    ? streamingConversationIds.includes(currentConversationId)
    : false

  /** 追加一条时间线事件到指定消息 */
  const pushTrace = useCallback((messageId: string, item: Omit<TraceItem, 'id' | 'at'>) => {
    setTraces((prev) =>
      capRecord({
        ...prev,
        [messageId]: [...(prev[messageId] || []), { ...item, id: nextTraceId(), at: Date.now() }],
      })
    )
  }, [])

  /** 加载当前会话的历史消息：先显示缓存，再后台刷新 */
  useEffect(() => {
    if (!currentConversationId) {
      setMessages([])
      return
    }
    // 先从缓存取，立即显示，避免切换时空白（用 getState 避免依赖循环）
    const cached = useUiStore.getState().messageCache[currentConversationId]
    if (cached && cached.length > 0) {
      setMessages(cached)
    } else {
      setMessages([])
    }

    let cancelled = false
    const load = async () => {
      try {
        const data = await api.conversations.get(currentConversationId)
        if (cancelled) return
        const msgs = ((data?.messages || []) as Message[]).map((m) => ({
          ...m,
          // 历史消息恢复交付文件：流式期间的 saved_files 已持久化到 metadata
          generatedFiles: m.generatedFiles || m.metadata?.savedFiles || undefined,
        }))
        setMessages(msgs)
        setLoadError(null)
      } catch (e) {
        if (cancelled) return
        console.error('[ChatView] 加载消息失败:', e)
        setLoadError('历史消息加载失败，请确认后端服务已启动')
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [currentConversationId, setMessages])

  /** 切换会话时不中断流式请求（流在后台继续运行） */
  useEffect(() => {
    // 不再 abort，流式请求由 store 按 conversationId 管理
  }, [currentConversationId])

  /** 自动滚动到底部 */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /** 发送消息 */
  const handleSend = async (text: string, images?: string[], attachments?: FileAttachment[]) => {
    if (!currentConversationId || isCurrentStreaming) return
    if (!text.trim() && (!images || images.length === 0) && (!attachments || attachments.length === 0)) return

    const userMsg: Message = {
      id: `local-user-${Date.now()}`,
      conversationId: currentConversationId,
      role: 'user',
      content: text || (images?.length ? '[图片]' : '[附件]'),
      images,
      attachments,
      createdAt: Date.now(),
    }
    appendMessage(userMsg)

    const assistantId = `local-assistant-${Date.now()}`
    const assistantMsg: Message = {
      id: assistantId,
      conversationId: currentConversationId,
      role: 'assistant',
      content: '',
      modelId: currentModelId,
      createdAt: Date.now(),
    }
    appendMessage(assistantMsg)

    // 第一条消息：异步调用 AI 自动提炼对话标题
    const currentConv = conversations.find((c) => c.id === currentConversationId)
    if (currentConv && (currentConv.title === '新对话' || !currentConv.title) && text.trim()) {
      const convId = currentConversationId
      const msgText = text
      const modelId = currentModelId
      ;(async () => {
        try {
          const res = await api.conversations.generateTitle(convId, msgText, modelId)
          if (res && res.ok && res.title) {
            setConversations(
              conversations.map((c) => (c.id === convId ? { ...c, title: res.title } : c))
            )
          }
        } catch {
          // 标题生成失败不影响对话
        }
      })()
    }

    await runStream(text, assistantId, images, attachments)
  }

  /** 发起 SSE 流式请求（按 conversationId 独立管理，切换对话不中断） */
  const runStream = async (text: string, assistantId: string, images?: string[], attachments?: FileAttachment[]) => {
    const conversationId = currentConversationId
    if (!conversationId) return

    // 如果该对话已有活跃流，先中断（同一对话不能并发两个流）
    const existingController = activeStreamControllers[conversationId]
    if (existingController) {
      existingController.abort()
    }

    const controller = new AbortController()
    registerActiveStream(conversationId, controller)

    let buffer = ''
    setIsStreaming(true, assistantId)

    // ── 流式合批：text/thinking delta 每个 token 都到，若逐 token 写 store，
    // 每条消息都会触发「整个消息数组 map + zustand set + 全列表重渲染」，
    // 长回复时明显卡顿。改为 requestAnimationFrame 合批——每帧最多提交一次，
    // 终态事件（error/done/onError/onDone）同步冲刷，保证最终内容不丢。
    let rafId: number | null = null
    let textDirty = false
    let pendingThinking = ''

    const flushNow = () => {
      if (rafId != null) {
        cancelAnimationFrame(rafId)
        rafId = null
      }
      if (pendingThinking) {
        const chunk = pendingThinking
        pendingThinking = ''
        // 函数式更新：始终基于最新 traces 决定「追加到末尾 thinking 块」
        // 还是「新建 thinking 时间线」（tool_call 之后会开新块）
        setTraces((prev) => {
          const list = prev[assistantId] || []
          const last = list[list.length - 1]
          let nextRec: Record<string, TraceItem[]>
          if (last && last.kind === 'thinking') {
            const next = [...list]
            next[next.length - 1] = { ...last, detail: (last.detail || '') + chunk }
            nextRec = { ...prev, [assistantId]: next }
          } else {
            nextRec = {
              ...prev,
              [assistantId]: [
                ...list,
                { kind: 'thinking', label: '思考中', detail: chunk, id: nextTraceId(), at: Date.now() },
              ],
            }
          }
          return capRecord(nextRec)
        })
      }
      if (textDirty) {
        textDirty = false
        updateMessageInConversation(conversationId, assistantId, { content: buffer })
      }
    }

    const scheduleFlush = () => {
      if (rafId != null) return
      rafId = requestAnimationFrame(flushNow)
    }

    await streamChat(
      { conversationId, message: text, modelId: currentModelId, images, attachments, mode: chatMode },
      {
        onEvent: (event) => {
          const data = event.data || {}

          switch (event.type) {
            case 'meta': {
              if (typeof data.usage === 'object' && data.usage) {
                const u = data.usage as { remaining?: number; total?: number }
                if (u.remaining != null && u.total != null) {
                  setTokenHint(
                    `剩余 ${u.remaining.toLocaleString()} / ${u.total.toLocaleString()} tokens`
                  )
                }
              }
              break
            }

            case 'thinking': {
              const delta = String(data.delta ?? data.text ?? '')
              if (!delta) break
              // 累积到 rAF 合批冲刷（每帧最多一次 setTraces）
              pendingThinking += delta
              scheduleFlush()
              break
            }

            case 'tool_call': {
              // 先冲刷待提交的 thinking，保证时间线顺序（thinking → 工具调用）
              flushNow()
              pushTrace(assistantId, {
                kind: 'tool_call',
                label: `调用工具：${String(data.name ?? data.tool ?? 'unknown')}`,
                detail: data.arguments != null ? String(data.arguments) : undefined,
              })
              break
            }

            case 'tool_result': {
              flushNow()
              pushTrace(assistantId, {
                kind: 'tool_result',
                label: `工具返回：${String(data.name ?? data.tool ?? 'unknown')}`,
                detail: String(data.result ?? data.output ?? ''),
                isError: Boolean(data.is_error ?? data.isError),
              })
              // 工具产出交付文件（filesystem write 成功）→ 渲染「打开文件 / 浏览文件夹」按钮
              const saved = data.saved_files ?? data.savedFiles
              if (Array.isArray(saved) && saved.length > 0) {
                const paths = saved.filter((p): p is string => typeof p === 'string' && p.startsWith('/'))
                if (paths.length > 0) {
                  const prev = generatedFilesRef.current[assistantId] || []
                  const merged = [...new Set([...prev, ...paths])]
                  generatedFilesRef.current[assistantId] = merged
                  updateMessageInConversation(conversationId, assistantId, { generatedFiles: merged })
                }
              }
              break
            }

            case 'citation': {
              const c = data as unknown as Citation
              setCitations((prev) =>
                capRecord({
                  ...prev,
                  [assistantId]: [...(prev[assistantId] || []), c],
                })
              )
              break
            }

            case 'text': {
              const delta = String(data.delta ?? data.text ?? data.content ?? '')
              if (!delta) break
              buffer += delta
              // 标记脏并按帧合批提交（按 conversationId 更新，
              // 切换对话后仍能正确写入该对话的缓存）
              textDirty = true
              scheduleFlush()
              break
            }

            case 'error': {
              flushNow()
              const msg = String(data.message ?? '未知错误')
              pushTrace(assistantId, { kind: 'error', label: '生成失败', detail: msg, isError: true })
              updateMessageInConversation(conversationId, assistantId, {
                content: buffer || `⚠ 生成失败：${msg}`,
              })
              break
            }

            case 'done': {
              flushNow()
              if (!buffer) {
                updateMessageInConversation(conversationId, assistantId, { content: '（无内容返回）' })
              }
              break
            }
          }
        },

        onError: (error) => {
          flushNow()
          pushTrace(assistantId, {
            kind: 'error',
            label: '连接错误',
            detail: error.message,
            isError: true,
          })
          updateMessageInConversation(conversationId, assistantId, { content: buffer || `⚠ ${error.message}` })
        },

        onDone: (aborted) => {
          flushNow()
          if (aborted && !buffer) {
            updateMessageInConversation(conversationId, assistantId, { content: '_（已停止生成）_' })
          } else if (aborted) {
            pushTrace(assistantId, { kind: 'error', label: '已被用户中断', isError: false })
          }
          setIsStreaming(false, null)
          clearActiveStream(conversationId)
        },
      },
      controller.signal
    )
  }

  /** 停止生成（仅停止当前对话的流） */
  const handleStop = () => {
    if (currentConversationId) {
      abortActiveStream(currentConversationId)
    }
  }

  /** 导出当前会话为 Markdown 文件（P1-2） */
  const [exporting, setExporting] = useState(false)
  const [exportMsg, setExportMsg] = useState('')
  const handleExport = async () => {
    if (!currentConversationId || exporting) return
    setExporting(true)
    setExportMsg('')
    try {
      const resp = await api.conversations.export(currentConversationId, 'md')
      const markdown =
        (resp && typeof resp === 'object' && (resp as any).markdown) ||
        (typeof resp === 'string' ? resp : '')
      if (!markdown) {
        setExportMsg('导出失败：后端未返回可导出的内容')
        return
      }
      const filename = (resp as any)?.filename || `对话导出-${Date.now()}.md`
      const result = await window.electronAPI?.saveFile({ defaultPath: filename, content: markdown })
      if (result?.ok) {
        setExportMsg(`已导出：${result.path || ''}`)
      } else if (result?.canceled) {
        // 用户取消保存，静默
      } else {
        setExportMsg(`导出失败：${result?.error || '保存文件失败'}`)
      }
    } catch (e) {
      setExportMsg(`导出失败：${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* 对话 / 工作 双模式切换栏 */}
      <div
        className="px-6 py-2 border-b flex items-center gap-1"
        style={{ borderBottom: '1px solid var(--border-soft)', background: 'var(--bg-panel)' }}
      >
        {(
          [
            { id: 'chat' as const, label: '对话', icon: '💬', desc: '日常聊天 · 轻量问答' },
            { id: 'work' as const, label: '工作', icon: '💼', desc: '专业任务 · 结构化交付' },
          ]
        ).map((m) => {
          const active = chatMode === m.id
          return (
            <button
              key={m.id}
              className="px-4 py-1.5 rounded-lg text-sm transition-colors"
              style={{
                background: active ? 'var(--accent-soft)' : 'transparent',
                color: active ? 'var(--accent)' : 'var(--text-dim)',
                border: 'none',
                cursor: 'pointer',
                fontWeight: active ? 600 : 400,
              }}
              onClick={() => setChatMode(m.id)}
            >
              <span className="mr-1">{m.icon}</span>
              {m.label}
              <span className="ml-1.5 text-xs opacity-70" style={{ color: active ? 'var(--accent)' : 'var(--text-faint)' }}>
                {m.desc}
              </span>
            </button>
          )
        })}
        <div className="ml-auto text-xs" style={{ color: 'var(--text-faint)' }}>
          {chatMode === 'chat' ? '对话分区 · 上下文与工作分区隔离' : '工作分区 · 上下文与对话分区隔离'}
        </div>
      </div>

      {/* 会话头部 */}
      <div
        className="px-6 py-3 border-b flex items-center justify-between"
        style={{ borderBottom: '1px solid var(--border-soft)' }}
      >
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            {chatMode === 'chat' ? '对话' : '工作'}
          </h1>
          <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-faint)' }}>
            <span>🔒</span>
            <span>127.0.0.1</span>
            <span>·</span>
            <span>{currentModel?.name || currentModelId}</span>
            {currentModel?.isPublic && (
              <span
                className="px-1.5 py-0.5 rounded"
                style={{ background: 'rgba(242,179,94,0.16)', color: 'var(--warn)' }}
              >
                公网
              </span>
            )}
          </div>
        </div>
        {!currentConversationId && (
          <div className="text-xs" style={{ color: 'var(--text-faint)' }}>
            请先在左侧新建或选择一个会话
          </div>
        )}
        {currentConversationId && (
          <div className="flex items-center gap-2">
            {exportMsg && (
              <span className="text-xs max-w-56 truncate" style={{ color: 'var(--text-faint)' }} title={exportMsg}>
                {exportMsg}
              </span>
            )}
            <button
              onClick={handleExport}
              disabled={exporting}
              className="text-xs px-3 py-1.5 rounded-lg transition-opacity hover:opacity-90 disabled:opacity-50"
              style={{ background: 'var(--bg-elev)', border: '1px solid var(--border-soft)', color: 'var(--text-dim)' }}
              title="导出当前会话为 Markdown"
            >
              {exporting ? '导出中…' : '导出'}
            </button>
          </div>
        )}
      </div>

      {/* 消息区 */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loadError && (
          <div
            className="max-w-3xl mx-auto mb-3 px-3 py-2 rounded-lg text-sm"
            style={{ background: 'rgba(242,100,124,0.12)', color: 'var(--danger)' }}
          >
            {loadError}
          </div>
        )}

        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="text-6xl mb-4">💬</div>
            <div className="text-xl font-semibold mb-2" style={{ color: 'var(--text)' }}>
              你好，我是颤翎子，开始对话
            </div>
            <div className="text-sm mb-6" style={{ color: 'var(--text-dim)' }}>
              选择模型，发送消息开始对话
            </div>
            <div className="grid grid-cols-2 gap-3 max-w-md">
              {QUICK_COMMANDS.map((cmd) => (
                <button
                  key={cmd.text}
                  className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm transition-colors"
                  style={{
                    background: 'var(--bg-panel)',
                    border: '1px solid var(--border-soft)',
                    color: 'var(--text-dim)',
                  }}
                  onClick={() => handleSend(cmd.text)}
                >
                  <span>{cmd.icon}</span>
                  <span>{cmd.text}</span>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto flex flex-col">
            {messages.map((msg) => (
              <MessageItem
                key={msg.id}
                message={msg}
                isSelected={selectedMessageId === msg.id}
                onSelect={setSelectedMessageId}
                trace={traces[msg.id]}
                citations={citations[msg.id]}
                streaming={isCurrentStreaming && streamingMessageId === msg.id}
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <InputBox
        onSend={handleSend}
        onStop={handleStop}
        isStreaming={isCurrentStreaming}
        disabled={!currentConversationId}
        tokenHint={tokenHint}
        models={models}
        currentModelId={currentModelId}
        onModelChange={setCurrentModelId}
        onNavigate={(tab) => setActiveTab(tab as any)}
      />
    </div>
  )
}

export default ChatView
