import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Conversation, Message, ModelInfo } from '../types'

interface UiState {
  // 路由与布局
  activeTab: 'chat' | 'knowledge' | 'tools' | 'usage' | 'settings'
  setActiveTab: (tab: UiState['activeTab']) => void

  // 主题
  theme: 'dark' | 'light'
  accent: 'teal' | 'violet' | 'amber' | 'rose'
  setTheme: (theme: UiState['theme'], accent?: UiState['accent']) => void

  // 当前会话
  conversations: Conversation[]
  currentConversationId: string | null
  setCurrentConversationId: (id: string) => void
  setConversations: (convs: Conversation[]) => void

  // 消息列表
  messages: Message[]
  setMessages: (msgs: Message[]) => void
  appendMessage: (msg: Message) => void
  updateMessage: (msgId: string, updates: Partial<Message>) => void

  // 模型
  models: ModelInfo[]
  currentModelId: string
  setModels: (models: ModelInfo[]) => void
  setCurrentModelId: (id: string) => void

  // 流式状态
  isStreaming: boolean
  streamingMessageId: string | null
  setIsStreaming: (isStreaming: boolean, messageId?: string | null) => void

  // 选中消息（用于右侧分析面板）
  selectedMessageId: string | null
  setSelectedMessageId: (id: string | null) => void
}

const DEFAULT_MODELS: ModelInfo[] = [
  { id: 'qwen2.5:7b', name: 'Qwen2.5 7B', providerId: 'ollama-local', isPublic: false },
  { id: 'deepseek-coder:6.7b', name: 'DeepSeek-Coder 6.7B', providerId: 'ollama-local', isPublic: false },
  { id: 'enterprise-v3', name: 'Enterprise-Model-V3', providerId: 'private-api', isPublic: false },
  { id: 'deepseek-chat', name: 'DeepSeek-Chat', providerId: 'cloud-deepseek', isPublic: true, description: '数据将发送至公网模型厂商' },
  { id: 'qwen-turbo', name: '通义千问 Turbo', providerId: 'cloud-qwen', isPublic: true, description: '数据将发送至公网模型厂商' },
]

export const useUiStore = create<UiState>()(
  persist(
    (set, get) => ({
      // 路由
      activeTab: 'chat',
      setActiveTab: (tab) => set({ activeTab: tab }),

      // 主题
      theme: 'dark',
      accent: 'teal',
      setTheme: (theme, accent) =>
        set({
          theme,
          accent: accent || get().accent,
        }),

      // 会话
      conversations: [],
      currentConversationId: null,
      setCurrentConversationId: (id) => set({ currentConversationId: id }),
      setConversations: (convs) => set({ conversations: convs }),

      // 消息
      messages: [],
      setMessages: (msgs) => set({ messages: msgs }),
      appendMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
      updateMessage: (msgId, updates) =>
        set((state) => ({
          messages: state.messages.map((m) =>
            m.id === msgId ? { ...m, ...updates } : m
          ),
        })),

      // 模型
      models: DEFAULT_MODELS,
      currentModelId: 'qwen2.5:7b',
      setModels: (models) => set({ models }),
      setCurrentModelId: (id) => set({ currentModelId: id }),

      // 流式状态
      isStreaming: false,
      streamingMessageId: null,
      setIsStreaming: (isStreaming, messageId) =>
        set({
          isStreaming,
          streamingMessageId: messageId || null,
        }),

      // 选中消息
      selectedMessageId: null,
      setSelectedMessageId: (id) => set({ selectedMessageId: id }),
    }),
    {
      name: 'private-ai-ui-state',
      partialize: (state) => ({
        theme: state.theme,
        accent: state.accent,
        conversations: state.conversations,
        currentConversationId: state.currentConversationId,
        models: state.models,
        currentModelId: state.currentModelId,
      }),
    }
  )
)
