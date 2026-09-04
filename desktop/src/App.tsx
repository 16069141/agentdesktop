import { useState, useEffect, useRef } from 'react'
import { useUiStore } from './store/useUiStore'
import { useTheme } from './theme'
import { api } from './api'
import Sidebar from './components/common/Sidebar'
import TopBar from './components/common/TopBar'
import ChatView from './components/chat/ChatView'
import KnowledgeView from './components/knowledge/KnowledgeView'
import ToolsView from './components/tools/ToolsView'
import UsageView from './components/usage/UsageView'
import SettingsView from './components/settings/SettingsView'
import type { Conversation, ModelInfo } from './types'

export default function App() {
  useTheme()

  const { activeTab, setConversations, setModels, currentConversationId, setCurrentConversationId } =
    useUiStore()

  const [phase, setPhase] = useState<'booting' | 'ready' | 'error'>('booting')
  const [errorMsg, setErrorMsg] = useState('')
  const bootedRef = useRef(false)

  /** 应用启动：等待后端健康检查 → 拉取会话与模型 */
  useEffect(() => {
    if (bootedRef.current) return
    bootedRef.current = true

    const boot = async () => {
      try {
        // 1) 等待后端就绪（Electron 主进程负责拉起 Agent）
        await waitHealthz(30_000)

        // 2) 拉取模型列表（失败则沿用本地默认列表）
        try {
          const models = (await api.models.list()) as ModelInfo[]
          if (Array.isArray(models) && models.length > 0) setModels(models)
        } catch {
          console.warn('[App] 模型列表拉取失败，使用本地默认列表')
        }

        // 3) 拉取会话列表；为空则自动创建首个会话
        const convs = (await api.conversations.list()) as Conversation[]
        const list = Array.isArray(convs) ? convs : []
        if (list.length === 0) {
          const created = (await api.conversations.create({
            title: '新对话',
            modelId: useUiStore.getState().currentModelId,
          })) as Conversation
          setConversations([created])
          setCurrentConversationId(created.id)
        } else {
          setConversations(list)
          if (!currentConversationId || !list.some((c) => c.id === currentConversationId)) {
            setCurrentConversationId(list[0].id)
          }
        }

        setPhase('ready')
      } catch (e) {
        console.error('[App] 启动失败:', e)
        setErrorMsg(e instanceof Error ? e.message : String(e))
        setPhase('error')
      }
    }

    boot()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (phase !== 'ready') {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: 'var(--bg)' }}
      >
        <div className="text-center max-w-md px-6">
          <div className="text-2xl font-semibold mb-2" style={{ color: 'var(--accent)' }}>
            PrivateAI
          </div>
          {phase === 'booting' ? (
            <div className="text-sm" style={{ color: 'var(--text-dim)' }}>
              正在启动本地服务…
              <div className="mt-1 text-xs" style={{ color: 'var(--text-faint)' }}>
                仅监听 127.0.0.1，数据不出本机
              </div>
            </div>
          ) : (
            <div className="text-sm" style={{ color: 'var(--danger)' }}>
              本地服务启动失败
              <div
                className="mt-2 text-xs px-3 py-2 rounded text-left"
                style={{ background: 'var(--code-bg)', fontFamily: 'var(--mono)' }}
              >
                {errorMsg}
              </div>
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="app flex h-screen overflow-hidden" style={{ fontFamily: 'var(--sans)' }}>
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">
        <TopBar />
        <div className="flex-1 overflow-hidden">
          {activeTab === 'chat' && <ChatView />}
          {activeTab === 'knowledge' && <KnowledgeView />}
          {activeTab === 'tools' && <ToolsView />}
          {activeTab === 'usage' && <UsageView />}
          {activeTab === 'settings' && <SettingsView />}
        </div>
      </main>
    </div>
  )
}

/** 轮询 /healthz 直到后端就绪，超时抛错 */
async function waitHealthz(timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs
  let lastErr: unknown = null

  while (Date.now() < deadline) {
    try {
      const ok = await api.health()
      if (ok) return
    } catch (e) {
      lastErr = e
    }
    await sleep(400)
  }
  throw new Error(`后端未在 ${timeoutMs / 1000}s 内就绪${lastErr ? `：${String(lastErr)}` : ''}`)
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
