import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useUiStore } from '../../store/useUiStore'
import { api } from '../../api'
import { streamChat } from '../../sse/sseClient'
import MessageItem from './MessageItem'
import InputBox from './InputBox'
import type { Message, TraceItem, Citation } from '../../types'

const QUICK_COMMANDS = [
  { text: '查公司请假制度', icon: '📋' },
  { text: '运行 ls 命令', icon: '💻' },
  { text: '分析一段代码', icon: '🔍' },
  { text: '总结项目进展', icon: '📊' },
]

/** 为流式过程中产生的事件生成稳定 key */
let traceSeq = 0
const nextTraceId = () => `trace-${Date.now()}-${traceSeq++}`

const ChatView: React.FC = () => {
  const {
    currentConversationId,
    messages,
    setMessages,
    appendMessage,
    updateMessage,
    isStreaming,
    setIsStreaming,
    streamingMessageId,
    selectedMessageId,
    setSelectedMessageId,
    currentModelId,
    models,
  } = useUiStore()

  /** 每条助理消息的事件时间线 */
  const [traces, setTraces] = useState<Record<string, TraceItem[]>>({})
  const [citations, setCitations] = useState<Record<string, Citation[]>>({})
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tokenHint, setTokenHint] = useState('剩余 14,200 / 16,384 tokens')

  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const currentModel = models.find((m) => m.id === currentModelId)

  /** 追加一条时间线事件到指定消息 */
  const pushTrace = useCallback((messageId: string, item: Omit<TraceItem, 'id' | 'at'>) => {
    setTraces((prev) => ({
      ...prev,
      [messageId]: [...(prev[messageId] || []), { ...item, id: nextTraceId(), at: Date.now() }],
    }))
  }, [])

  /** 增量更新最后一条同类时间线事件（用于 thinking 流式追加） */
  const patchLastTrace = useCallback(
    (messageId: string, kind: TraceItem['kind'], patch: Partial<TraceItem>) => {
      setTraces((prev) => {
        const list = prev[messageId]
        if (!list || list.length === 0) return prev
        const idx = [...list].reverse().findIndex((t) => t.kind === kind)
        if (idx === -1) return prev
        const realIdx = list.length - 1 - idx
        const next = [...list]
        next[realIdx] = { ...next[realIdx], ...patch }
        return { ...prev, [messageId]: next }
      })
    },
    []
  )

  /** 加载当前会话的历史消息 */
  useEffect(() => {
    if (!currentConversationId) {
      setMessages([])
      return
    }
    let cancelled = false
    const load = async () => {
      try {
        const data = await api.conversations.get(currentConversationId)
        if (cancelled) return
        setMessages((data?.messages || []) as Message[])
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

  /** 切换会话时中断进行中的流 */
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [currentConversationId])

  /** 自动滚动到底部 */
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  /** 发送消息 */
  const handleSend = async (text: string) => {
    if (!currentConversationId || isStreaming || !text.trim()) return

    const userMsg: Message = {
      id: `local-user-${Date.now()}`,
      conversationId: currentConversationId,
      role: 'user',
      content: text,
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

    await runStream(text, assistantId)
  }

  /** 发起 SSE 流式请求 */
  const runStream = async (text: string, assistantId: string) => {
    const conversationId = currentConversationId
    if (!conversationId) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    let buffer = ''
    setIsStreaming(true, assistantId)

    await streamChat(
      { conversationId, message: text, modelId: currentModelId },
      {
        onEvent: (event) => {
          const data = event.data || {}

          switch (event.type) {
            case 'meta': {
              // 服务端返回的真实 message_id / 用量信息
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
              const list = traces[assistantId] || []
              const last = list[list.length - 1]
              if (last && last.kind === 'thinking') {
                patchLastTrace(assistantId, 'thinking', { detail: (last.detail || '') + delta })
              } else {
                pushTrace(assistantId, { kind: 'thinking', label: '思考中', detail: delta })
              }
              break
            }

            case 'tool_call': {
              pushTrace(assistantId, {
                kind: 'tool_call',
                label: `调用工具：${String(data.name ?? data.tool ?? 'unknown')}`,
                detail: data.arguments != null ? String(data.arguments) : undefined,
              })
              break
            }

            case 'tool_result': {
              pushTrace(assistantId, {
                kind: 'tool_result',
                label: `工具返回：${String(data.name ?? data.tool ?? 'unknown')}`,
                detail: String(data.result ?? data.output ?? ''),
                isError: Boolean(data.is_error ?? data.isError),
              })
              break
            }

            case 'citation': {
              const c = data as unknown as Citation
              setCitations((prev) => ({
                ...prev,
                [assistantId]: [...(prev[assistantId] || []), c],
              }))
              break
            }

            case 'text': {
              const delta = String(data.delta ?? data.text ?? data.content ?? '')
              if (!delta) break
              buffer += delta
              updateMessage(assistantId, { content: buffer })
              break
            }

            case 'error': {
              const msg = String(data.message ?? '未知错误')
              pushTrace(assistantId, { kind: 'error', label: '生成失败', detail: msg, isError: true })
              updateMessage(assistantId, {
                content: buffer || `⚠ 生成失败：${msg}`,
              })
              break
            }

            case 'done': {
              if (!buffer) {
                updateMessage(assistantId, { content: '（无内容返回）' })
              }
              break
            }
          }
        },

        onError: (error) => {
          pushTrace(assistantId, {
            kind: 'error',
            label: '连接错误',
            detail: error.message,
            isError: true,
          })
          updateMessage(assistantId, { content: buffer || `⚠ ${error.message}` })
        },

        onDone: (aborted) => {
          if (aborted && !buffer) {
            updateMessage(assistantId, { content: '_（已停止生成）_' })
          } else if (aborted) {
            pushTrace(assistantId, { kind: 'error', label: '已被用户中断', isError: false })
          }
          setIsStreaming(false, null)
          abortRef.current = null
        },
      },
      controller.signal
    )
  }

  /** 停止生成 */
  const handleStop = () => {
    abortRef.current?.abort()
  }

  return (
    <div className="flex flex-col h-full">
      {/* 会话头部 */}
      <div
        className="px-6 py-3 border-b flex items-center justify-between"
        style={{ borderBottom: '1px solid var(--border-soft)' }}
      >
        <div>
          <h1 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            对话
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
              开始对话
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
                streaming={isStreaming && streamingMessageId === msg.id}
              />
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      <InputBox
        onSend={handleSend}
        onStop={handleStop}
        isStreaming={isStreaming}
        disabled={!currentConversationId}
        tokenHint={tokenHint}
      />
    </div>
  )
}

export default ChatView
