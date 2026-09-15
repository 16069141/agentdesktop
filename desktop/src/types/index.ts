export interface Conversation {
  id: string
  title: string
  modelId: string
  mode?: string
  /** 工作模式下会话绑定的工作目录绝对路径 */
  workspacePath?: string | null
  createdAt: number
  updatedAt: number
}

/** 文档附件：由前端上传解析后随消息提交 */
export interface FileAttachment {
  filename: string
  kind: 'text' | 'pdf' | 'docx' | 'xlsx' | 'pptx'
  extracted_text: string
  char_count: number
  size?: number
  truncated?: boolean
  /** 上传落盘后的绝对路径（后端 files/upload 返回），模型可用 filesystem 工具读取原始文件 */
  savedPath?: string
}

export interface Message {
  id: string
  conversationId: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  modelId?: string
  createdAt: number
  /** 粘贴的图片（base64 data URL），仅当前轮次有效 */
  images?: string[]
  /** 文档附件，仅当前轮次有效 */
  attachments?: FileAttachment[]
  /** Agent 工具生成的交付文件路径（如 filesystem write 落盘），渲染「打开/浏览」按钮 */
  generatedFiles?: string[]
  /** P0 智能体计划：assistant 消息执行中的步骤清单（create_plan/update_plan 事件） */
  plan?: PlanStep[]
  /** P0 后台任务：后台执行的进度/状态句柄 */
  task?: TaskMeta
  /** 服务端持久化的消息元数据（assistant 消息含 savedFiles 交付文件，历史重载时恢复） */
  metadata?: { savedFiles?: string[] } | null
}

export interface ToolCall {
  id: string
  messageId: string
  callId: string
  name: string
  arguments: string
  result?: string
  isError: boolean
  approved?: number
  createdAt: number
}

export interface SSEEvent {
  type: 'meta' | 'thinking' | 'tool_call' | 'tool_result' | 'plan' | 'text' | 'done' | 'error'
  data: any
}

/** P0 智能体计划：一步的 id/标题/状态（pending/running/done/failed/skipped） */
export interface PlanStep {
  id: string
  title: string
  detail?: string
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped'
  note?: string
}

/** P0 后台任务状态 */
export type TaskStatus = 'queued' | 'running' | 'done' | 'failed' | 'cancelled'

/** P0 后台任务：assistant 消息挂载的后台执行句柄 */
export interface TaskMeta {
  taskId: string
  status: TaskStatus
  /** 发起任务的原始用户消息（续跑/重试复用） */
  message: string
}

/** 单条助理消息的事件时间线项（思考 / 工具调用 / 工具结果 / 引用 / 错误） */
export interface TraceItem {
  id: string
  kind: 'thinking' | 'tool_call' | 'tool_result' | 'citation' | 'error'
  label: string
  detail?: string
  isError?: boolean
  at: number
}

/** 引用来源 */
export interface Citation {
  docId: string
  docName: string
  chunkIndex?: number
  score?: number
  snippet?: string
}

export interface ChatRequest {
  conversationId: string
  message: string
  modelId: string
}

export interface ChatResponse {
  conversationId: string
  messageId: string
  usage?: { promptTokens: number; completionTokens: number }
}

export interface ModelInfo {
  id: string
  name: string
  providerId: string
  isPublic: boolean
  /** 数据去向：local=本机 / lan=局域网 / public=公网 / unknown=未知 */
  scope?: 'local' | 'lan' | 'public' | 'unknown'
  description?: string
}

export interface Provider {
  id: string
  type: 'ollama' | 'openai-compatible'
  baseUrl: string
  apiKeyRef?: string
  models: string[]
  isPublic?: boolean
}

export interface UsageEntry {
  id: number
  conversationId: string
  modelId: string
  promptTokens: number
  completionTokens: number
  toolCalls: number
  cost: number
  createdAt: number
}
