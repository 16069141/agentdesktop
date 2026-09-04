export interface Conversation {
  id: string
  title: string
  modelId: string
  createdAt: number
  updatedAt: number
}

export interface Message {
  id: string
  conversationId: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  modelId?: string
  createdAt: number
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
  type: 'meta' | 'thinking' | 'tool_call' | 'tool_result' | 'text' | 'done' | 'error'
  data: any
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
