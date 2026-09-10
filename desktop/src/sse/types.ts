import type { Conversation, Message, ModelInfo, FileAttachment } from '../types'

/** SSE 事件类型（对齐规格书 §7.1） */
export type SSEEventType =
  | 'meta'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'citation'
  | 'text'
  | 'done'
  | 'error'

/** 单条已解析完成的 SSE 事件 */
export interface SSEEvent {
  type: SSEEventType
  data: Record<string, unknown> | null
}

/** 流式对话请求参数 */
export interface ChatStreamParams {
  conversationId: string
  message: string
  modelId: string
  /** 粘贴的图片（base64 data URL） */
  images?: string[]
  /** 文档附件（已由前端上传解析） */
  attachments?: FileAttachment[]
  /** 对话/工作双模式分区 */
  mode?: 'chat' | 'work'
}

/** 流式回调集合 */
export interface ChatStreamHandlers {
  /** 每解析出一条完整事件即回调（含 done / error） */
  onEvent: (event: SSEEvent) => void
  /** 传输层或服务端出错（用户主动中断不会触发） */
  onError?: (error: Error) => void
  /** 流结束（正常结束或被中断都会触发），aborted 标识是否为中断 */
  onDone?: (aborted: boolean) => void
}

export type { Conversation, Message, ModelInfo, FileAttachment }
