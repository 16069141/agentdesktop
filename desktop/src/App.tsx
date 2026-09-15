import { useState, useEffect, useRef } from 'react'
import { useUiStore } from './store/useUiStore'
import { useTheme } from './theme'
import { api } from './api'
import parrotIcon from './assets/parrot-icon.png'
import Sidebar from './components/common/Sidebar'
import TopBar from './components/common/TopBar'
import ChatView from './components/chat/ChatView'
import KnowledgeView from './components/knowledge/KnowledgeView'
import ToolsView from './components/tools/ToolsView'
import UsageView from './components/usage/UsageView'
import AuditView from './components/audit/AuditView'
import SkillsView from './components/skills/SkillsView'
import MemoryView from './components/memory/MemoryView'
import ScheduleView from './components/schedule/ScheduleView'
import ConnectorsView from './components/connectors/ConnectorsView'
import ProjectsView from './components/projects/ProjectsView'
import WorkflowsView from './components/workflows/WorkflowsView'
import SettingsView from './components/settings/SettingsView'
import type { Conversation, ModelInfo } from './types'

export default function App() {
  useTheme()

  const { activeTab, setConversations, setModels, setCurrentConversationId, chatMode } =
    useUiStore()

  const [phase, setPhase] = useState<'booting' | 'ready' | 'error'>('booting')
  const [errorMsg, setErrorMsg] = useState('')
  const bootedRef = useRef(false)

  /** 刷新模型列表 */
  const refreshModels = async () => {
    try {
      const models = (await api.models.list()) as ModelInfo[]
      if (Array.isArray(models) && models.length > 0) {
        setModels(models)
        // 如果当前模型不在列表中，自动选择第一个
        const currentModelId = useUiStore.getState().currentModelId
        if (!models.some((m) => m.id === currentModelId)) {
          useUiStore.getState().setCurrentModelId(models[0].id)
        }
      }
    } catch {
      console.warn('[App] 模型列表刷新失败')
    }
  }

  /** 加载指定模式（对话/工作）的会话列表；为空自动新建首个会话 */
  const loadConversations = async (mode: 'chat' | 'work') => {
    try {
      const list = (await api.conversations.list(mode).catch(() => [])) as Conversation[]
      if (list.length === 0) {
        const created = (await api.conversations.create({
          title: '新对话',
          modelId: useUiStore.getState().currentModelId,
          mode,
        })) as Conversation
        setConversations([created])
        setCurrentConversationId(created.id)
        return created.id
      }
      setConversations(list)
      // 恢复该模式上次停留的会话；不存在则取最新
      const savedId = useUiStore.getState().modeCurrentIds[mode]
      const target = savedId && list.some((c) => c.id === savedId) ? savedId : list[0].id
      setCurrentConversationId(target)
      return target
    } catch (e) {
      console.error(`[App] 加载${mode}会话失败:`, e)
      return null
    }
  }

  /** 应用启动：等待后端就绪 → 拉模型 → 加载当前模式会话 */
  useEffect(() => {
    if (bootedRef.current) return
    bootedRef.current = true

    const boot = async () => {
      try {
        // 1) 等待后端就绪：优先 IPC 通知，回退到轮询
        await waitForBackend(30_000)
        // 2) 拉取模型列表
        await refreshModels()
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

  /** 当前模式会话加载：启动就绪后 / 切换 对话↔工作 Tab 时触发 */
  useEffect(() => {
    if (phase !== 'ready') return
    loadConversations(chatMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatMode, phase])

  if (phase !== 'ready') {
    return (
      <div
        className="flex items-center justify-center h-screen"
        style={{ background: 'var(--bg)' }}
      >
        <div className="text-center max-w-md px-6">
          <div className="flex justify-center mb-4">
            <img src={parrotIcon} alt="颤翎子" className="w-16 h-16 rounded-2xl object-cover" style={{ boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }} />
          </div>
          <div className="text-2xl font-semibold mb-2" style={{ color: 'var(--accent)' }}>
            颤翎子AI助手
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
          {activeTab === 'audit' && <AuditView />}
          {activeTab === 'skills' && <SkillsView />}
          {activeTab === 'memory' && <MemoryView />}
          {activeTab === 'schedule' && <ScheduleView />}
          {activeTab === 'connectors' && <ConnectorsView />}
          {activeTab === 'projects' && <ProjectsView />}
          {activeTab === 'workflows' && <WorkflowsView />}
          {activeTab === 'settings' && <SettingsView />}
        </div>
      </main>
    </div>
  )
}

/** 等待后端就绪：优先 IPC agent-ready 通知，回退到轮询 */
async function waitForBackend(timeoutMs: number): Promise<void> {
  // IPC 通知（主进程 Agent 就绪后立即触发，无需轮询等待）
  const ipcReady = new Promise<void>((resolve) => {
    window.electronAPI?.onAgentReady?.(() => resolve())
  })

  // 轮询兜底（IPC 未到达或非 Electron 环境时）
  const pollingReady = waitHealthz(timeoutMs)

  await Promise.race([ipcReady, pollingReady])
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
