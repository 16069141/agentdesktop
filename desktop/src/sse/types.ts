import type { Conversation, Message, ModelInfo, FileAttachment } from '../types'

/** SSE 事件类型（对齐规格书 §7.1；plan 为 P0 智能体计划事件；plan_awaiting_confirm 为 P0.5 Plan 模式待确认事件） */
export type SSEEventType =
  | 'meta'
  | 'thinking'
  | 'tool_call'
  | 'tool_result'
  | 'citation'
  | 'plan'
  | 'plan_awaiting_confirm'
  | 'text'
  | 'done'
  | 'error'

/** 单条已解析完成的 SSE 事件 */
export interface SSEEvent {
  type: SSEEventType
  data: Record<string, unknown> | null
}

/** P0.5 三种工作模式（对齐 WorkBuddy Craft/Plan/Ask） */
export type WorkMode = 'craft' | 'plan' | 'ask'

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
  /** 工作模式下绑定的工作目录绝对路径（文件/shell 操作的根） */
  workspaceDir?: string
  /** P0.5 工作模式：craft=直接执行 / plan=先计划后确认 / ask=只答不动 */
  workMode?: WorkMode
  /** Plan 模式下用户已确认计划（第二轮请求置 True） */
  planConfirmed?: boolean
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
