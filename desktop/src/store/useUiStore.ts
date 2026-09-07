import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { Conversation, Message, ModelInfo } from '../types'

interface UiState {
  // 路由与布局（Phase B P0：新增 audit / skills；P1：新增 connectors；P2：新增 projects；P3：新增 workflows）
  activeTab: 'chat' | 'knowledge' | 'tools' | 'usage' | 'settings' | 'audit' | 'skills' | 'connectors' | 'projects' | 'workflows'
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
  /** 按 conversationId 缓存的消息，切换对话时直接从缓存取，避免丢失 */
  messageCache: Record<string, Message[]>
  /** 将当前 messages 保存到对应 conversationId 的缓存 */
  cacheMessages: (conversationId: string) => void
  /** 从缓存取指定对话的消息 */
  getCachedMessages: (conversationId: string) => Message[]
  /** 按 conversationId 更新指定对话的消息（用于后台流式输出，不依赖当前查看的对话） */
  updateMessageInConversation: (conversationId: string, msgId: string, updates: Partial<Message>) => void
  /** 按 conversationId 追加消息到指定对话的缓存 */
  appendMessageToConversation: (conversationId: string, msg: Message) => void

  // 流式请求管理（按 conversationId 独立，切换对话不中断）
  activeStreamControllers: Record<string, AbortController>
  streamingConversationIds: string[]
  registerActiveStream: (conversationId: string, controller: AbortController) => void
  abortActiveStream: (conversationId: string) => void
  clearActiveStream: (conversationId: string) => void

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

// 纯云端/连接式架构：不内置任何本地模型清单，
// 模型列表完全来自「模型服务器连接」的动态探测
const DEFAULT_MODELS: ModelInfo[] = []

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
      messageCache: {},
      setMessages: (msgs) =>
        set((state) => {
          const cache = { ...state.messageCache }
          if (state.currentConversationId) {
            cache[state.currentConversationId] = msgs
          }
          return { messages: msgs, messageCache: cache }
        }),
      appendMessage: (msg) =>
        set((state) => {
          const next = [...state.messages, msg]
          const cache = { ...state.messageCache }
          if (state.currentConversationId) {
            cache[state.currentConversationId] = next
          }
          return { messages: next, messageCache: cache }
        }),
      updateMessage: (msgId, updates) =>
        set((state) => {
          const next = state.messages.map((m) =>
            m.id === msgId ? { ...m, ...updates } : m
          )
          const cache = { ...state.messageCache }
          if (state.currentConversationId) {
            cache[state.currentConversationId] = next
          }
          return { messages: next, messageCache: cache }
        }),
      cacheMessages: (conversationId) =>
        set((state) => ({
          messageCache: {
            ...state.messageCache,
            [conversationId]: state.messages,
          },
        })),
      getCachedMessages: (conversationId) => {
        const state = get()
        return state.messageCache[conversationId] || []
      },
      updateMessageInConversation: (conversationId, msgId, updates) =>
        set((state) => {
          // 更新指定对话的缓存
          const cache = { ...state.messageCache }
          const convMsgs = cache[conversationId] || []
          cache[conversationId] = convMsgs.map((m) =>
            m.id === msgId ? { ...m, ...updates } : m
          )
          // 如果当前正在查看该对话，同时更新 messages
          const isCurrent = state.currentConversationId === conversationId
          return {
            messageCache: cache,
            messages: isCurrent ? cache[conversationId] : state.messages,
          }
        }),
      appendMessageToConversation: (conversationId, msg) =>
        set((state) => {
          const cache = { ...state.messageCache }
          cache[conversationId] = [...(cache[conversationId] || []), msg]
          const isCurrent = state.currentConversationId === conversationId
          return {
            messageCache: cache,
            messages: isCurrent ? cache[conversationId] : state.messages,
          }
        }),

      // 流式请求管理
      activeStreamControllers: {},
      streamingConversationIds: [],
      registerActiveStream: (conversationId, controller) =>
        set((state) => ({
          activeStreamControllers: { ...state.activeStreamControllers, [conversationId]: controller },
          streamingConversationIds: state.streamingConversationIds.includes(conversationId)
            ? state.streamingConversationIds
            : [...state.streamingConversationIds, conversationId],
        })),
      abortActiveStream: (conversationId) => {
        const state = get()
        const controller = state.activeStreamControllers[conversationId]
        if (controller) {
          controller.abort()
        }
      },
      clearActiveStream: (conversationId) =>
        set((state) => {
          const controllers = { ...state.activeStreamControllers }
          delete controllers[conversationId]
          return {
            activeStreamControllers: controllers,
            streamingConversationIds: state.streamingConversationIds.filter((id) => id !== conversationId),
          }
        }),

      // 模型
      models: DEFAULT_MODELS,
      currentModelId: '',
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
