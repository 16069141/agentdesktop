import { apiBase } from '../api'
import type { SSEEvent, SSEEventType, ChatStreamParams, ChatStreamHandlers } from './types'

/**
 * SSE 协议常量
 * - 事件之间以空行（\n\n）分隔
 * - 兼容 CRLF（\r\n\r\n）与旧式 CR（\r\r）
 * - 流结束哨兵：data: [DONE]
 */
const EVENT_SEPARATOR = '\n\n'
const DONE_SENTINEL = '[DONE]'

const KNOWN_EVENT_TYPES: SSEEventType[] = [
  'meta',
  'thinking',
  'tool_call',
  'tool_result',
  'citation',
  'text',
  'done',
  'error',
]

function isKnownEventType(value: string): value is SSEEventType {
  return (KNOWN_EVENT_TYPES as string[]).includes(value)
}

/**
 * SSE 增量解码器。
 *
 * 存在的意义：TCP / HTTP chunk 不保证按 SSE 事件边界切分，
 * 一次 chunk 可能包含「半个事件」（半包）或「多个事件拼在一起」（粘包）。
 * 本解码器维护一个跨 chunk 的残留缓冲，只在遇到完整空行分隔时才吐出事件。
 *
 * 用法：
 *   const decoder = new SSEDecoder()
 *   events = decoder.feed(textChunk)   // 每个网络分片调用一次
 *   events = decoder.flush()           // 流结束后调用，冲刷尾部残留
 */
export class SSEDecoder {
  private buffer = ''

  /** 追加一片网络数据，返回其中已完整的事件列表 */
  feed(chunk: string): SSEEvent[] {
    if (!chunk) return []
    // 统一换行符：CRLF / CR 全部归一为 LF，避免分隔符识别失败
    const normalized = chunk.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
    this.buffer += normalized

    const events: SSEEvent[] = []
    let sepIndex = this.buffer.indexOf(EVENT_SEPARATOR)

    // 只要还能找到分隔符，就说明至少有一个完整事件
    while (sepIndex !== -1) {
      const block = this.buffer.slice(0, sepIndex)
      this.buffer = this.buffer.slice(sepIndex + EVENT_SEPARATOR.length)
      const parsed = parseEventBlock(block)
      if (parsed) events.push(parsed)
      sepIndex = this.buffer.indexOf(EVENT_SEPARATOR)
    }

    return events
  }

  /**
   * 流结束时冲刷残留缓冲。
   * 服务端若未在末尾补空行（即最后一个事件没有分隔符收尾），
   * 它会一直留在 buffer 里，这里兜底吐出，避免丢最后一条。
   */
  flush(): SSEEvent[] {
    const rest = this.buffer
    this.buffer = ''
    if (!rest.trim()) return []

    const events: SSEEvent[] = []
    for (const part of rest.split(EVENT_SEPARATOR)) {
      const parsed = parseEventBlock(part)
      if (parsed) events.push(parsed)
    }
    return events
  }

  /** 丢弃缓冲（断开连接 / 重置状态时使用） */
  reset(): void {
    this.buffer = ''
  }
}

/**
 * 解析单个 SSE block（形如 "event: text\ndata: {...}"）。
 * 遵循 WHATWG SSE 规范：
 *  - 以冒号开头的行是注释，忽略
 *  - 字段名取第一个冒号前的内容，值去掉首个空格
 *  - 多行 data 用 \n 拼接
 */
export function parseEventBlock(block: string): SSEEvent | null {
  if (!block || !block.trim()) return null

  let eventName = ''
  const dataLines: string[] = []

  for (const rawLine of block.split('\n')) {
    if (rawLine === '') continue
    if (rawLine.startsWith(':')) continue // 注释 / 心跳

    const colonIndex = rawLine.indexOf(':')
    let field: string
    let value: string

    if (colonIndex === -1) {
      field = rawLine
      value = ''
    } else {
      field = rawLine.slice(0, colonIndex)
      value = rawLine.slice(colonIndex + 1)
      if (value.startsWith(' ')) value = value.slice(1)
    }

    if (field === 'event') {
      eventName = value.trim()
    } else if (field === 'data') {
      dataLines.push(value)
    }
    // id / retry 字段本阶段不使用，忽略
  }

  if (dataLines.length === 0) return null

  const rawData = dataLines.join('\n')
  if (!rawData.trim()) return null

  // 流结束哨兵
  if (rawData.trim() === DONE_SENTINEL) {
    return { type: 'done', data: null }
  }

  // 尝试按 JSON 解析
  let payload: Record<string, unknown> | null = null
  try {
    payload = JSON.parse(rawData) as Record<string, unknown>
  } catch {
    payload = null
  }

  if (payload) {
    // 事件名优先取 event: 行，其次取 payload 内的 type 字段
    const innerType = typeof payload.type === 'string' ? payload.type : ''
    const resolved = eventName || innerType
    const type: SSEEventType = isKnownEventType(resolved) ? resolved : 'text'
    return { type, data: payload }
  }

  // 非 JSON：降级为纯文本增量
  return {
    type: isKnownEventType(eventName) ? eventName : 'text',
    data: { delta: rawData },
  }
}

/**
 * 发起一次流式对话（POST /api/chat，响应为 text/event-stream）。
 *
 * 中断：传入 signal（AbortController.signal），调用 abort() 后：
 *  - fetch 会中止读取，reader.read() 抛出 AbortError
 *  - 本函数不会调用 onError，而是 onDone(true)
 *
 * 注意：本函数内部绝不因 AbortError 抛错到调用方（除未知异常外），
 * 保证调用方的 finally 逻辑可统一收尾。
 */
export async function streamChat(
  params: ChatStreamParams,
  handlers: ChatStreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  const { onEvent, onError, onDone } = handlers

  // 中断状态：AbortController 触发时置位，用于区分「用户中断」与「真实错误」
  let aborted = false
  const onAbort = () => {
    aborted = true
  }
  if (signal) {
    if (signal.aborted) aborted = true
    else signal.addEventListener('abort', onAbort, { once: true })
  }

  try {
    const token = (await window.electronAPI?.getAgentToken()) || ''
    if (!token) {
      throw new Error('未获取到本地鉴权 Token，后端服务可能未启动')
    }

    const resp = await fetch(`${apiBase}/api/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({
        conversation_id: params.conversationId,
        message: params.message,
        model_id: params.modelId,
        images: params.images || [],
        attachments: params.attachments || [],
        mode: params.mode || 'chat',
      }),
      signal,
    })

    if (!resp.ok) {
      const detail = await resp.text().catch(() => '')
      throw new Error(`HTTP ${resp.status} ${resp.statusText}${detail ? ` - ${detail}` : ''}`)
    }

    const reader = resp.body?.getReader()
    if (!reader) {
      throw new Error('响应体不可读（当前环境不支持 ReadableStream）')
    }

    const decoder = new TextDecoder('utf-8')
    const sse = new SSEDecoder()

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      // stream: true —— 关键：多字节 UTF-8 字符可能跨 chunk 截断，
      // 交给 TextDecoder 内部缓冲，避免中文乱码
      const chunk = decoder.decode(value, { stream: true })
      for (const event of sse.feed(chunk)) {
        onEvent(event)
      }
    }

    // 冲刷残留：服务端若用 data: [DONE] 结尾则此处通常无残留
    for (const event of sse.flush()) {
      onEvent(event)
    }

    // 兼容「服务端未显式发 done」的情况，确保调用方一定能收尾
    onDone?.(false)
  } catch (err: unknown) {
    const error = err instanceof Error ? err : new Error(String(err))

    // 用户主动中断：视为正常结束，不走错误通道
    if (error.name === 'AbortError' || aborted) {
      onDone?.(true)
      return
    }

    onError?.(error)
    onEvent({ type: 'error', data: { message: error.message } })
    onDone?.(false)
  } finally {
    if (signal) signal.removeEventListener('abort', onAbort)
  }
}
