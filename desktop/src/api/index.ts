export const apiBase = 'http://127.0.0.1:8766'

async function request<T>(
  method: string,
  path: string,
  options?: { body?: unknown }
): Promise<T> {
  const token = await window.electronAPI?.getAgentToken() || ''
  const resp = await fetch(`${apiBase}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(options?.body !== undefined ? { body: JSON.stringify(options.body) } : {}),
  })

  if (!resp.ok) {
    const text = await resp.text()
    throw new Error(`HTTP ${resp.status}: ${text}`)
  }

  return resp.json() as Promise<T>
}

/** multipart/form-data 文件上传（XHR 实现，支持进度回调与取消） */
function uploadFile<T>(
  path: string,
  file: File,
  onProgress?: (percent: number) => void,
): { promise: Promise<T>; abort: () => void } {
  const xhr = new XMLHttpRequest()
  const formData = new FormData()
  formData.append('file', file)

  const promise = new Promise<T>((resolve, reject) => {
    xhr.open('POST', `${apiBase}${path}`)
    window.electronAPI?.getAgentToken().then((token) => {
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)
      // 上传进度（仅上传阶段）
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100))
        }
      }
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            resolve(JSON.parse(xhr.responseText) as T)
          } catch {
            reject(new Error('响应解析失败'))
          }
        } else {
          reject(new Error(`HTTP ${xhr.status}: ${xhr.responseText}`))
        }
      }
      xhr.onerror = () => reject(new Error('网络错误，请检查本地服务'))
      xhr.onabort = () => reject(new Error('已取消上传'))
      xhr.send(formData)
    })
  })

  return { promise, abort: () => xhr.abort() }
}

export const api = {
  // 健康检查
  health: () => request<{ ok: boolean }>('GET', '/healthz'),

  // 会话 CRUD
  conversations: {
    list: (mode?: string) =>
      request<any[]>('GET', `/api/conversations${mode ? `?mode=${mode}` : ''}`),
    create: (data: { title: string; modelId: string; mode?: string; workspacePath?: string }) =>
      request<any>('POST', '/api/conversations', { body: data }),
    get: (id: string) => request<any>('GET', `/api/conversations/${encodeURIComponent(id)}`),
    delete: (id: string) => request<any>('DELETE', `/api/conversations/${encodeURIComponent(id)}`),
    update: (id: string, data: { title?: string; modelId?: string }) =>
      request<any>('PATCH', `/api/conversations/${encodeURIComponent(id)}`, { body: data }),
    generateTitle: (id: string, message: string, modelId?: string) =>
      request<any>('POST', `/api/conversations/${encodeURIComponent(id)}/generate-title`, { body: { message, modelId } }),
    export: (id: string, format: 'md' | 'json') =>
      request<string>('POST', `/api/conversations/${encodeURIComponent(id)}/export?format=${format}`),
  },

  // 模型
  models: {
    list: () => request<any[]>('GET', '/api/models'),
    refresh: () => request<any[]>('POST', '/api/models/refresh'),
  },

  // 模型服务器连接（局域网/互联网大模型服务器）
  llmServers: {
    list: () => request<any[]>('GET', '/api/llm-servers'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/llm-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/llm-servers/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/llm-servers/${encodeURIComponent(id)}`),
    // 组件常用名别名：delete / healthCheck
    delete: (id: string) => request<any>('DELETE', `/api/llm-servers/${encodeURIComponent(id)}`),
    healthCheck: (id: string) =>
      request<any>('POST', `/api/llm-servers/${encodeURIComponent(id)}/test`),
    test: (id: string) => request<any>('POST', `/api/llm-servers/${encodeURIComponent(id)}/test`),
    syncModels: (id: string) =>
      request<any>('POST', `/api/llm-servers/${encodeURIComponent(id)}/sync-models`),
    models: (id: string) => request<any>('GET', `/api/llm-servers/${encodeURIComponent(id)}/models`),
    getApiKey: (id: string) => request<any>('GET', `/api/llm-servers/${encodeURIComponent(id)}/api-key`),
  },

  // 知识库连接（局域网/互联网 RAG、LLM Wiki 等）
  knowledgeServers: {
    list: () => request<any[]>('GET', '/api/knowledge-servers'),
    platforms: () => request<any[]>('GET', '/api/knowledge-servers/platforms'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/knowledge-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/knowledge-servers/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/knowledge-servers/${encodeURIComponent(id)}`),
    test: (id: string) => request<any>('POST', `/api/knowledge-servers/${encodeURIComponent(id)}/test`),
    search: (id: string, query: string, topK = 5, topic?: string) =>
      request<any>('POST', `/api/knowledge-servers/${encodeURIComponent(id)}/search`, {
        body: { query, top_k: topK, topic: topic || undefined },
      }),
    spaces: (id: string) => request<any>('GET', `/api/knowledge-servers/${encodeURIComponent(id)}/spaces`),
    topics: (id: string) => request<any>('GET', `/api/knowledge-servers/${encodeURIComponent(id)}/topics`),
    getApiKey: (id: string) => request<any>('GET', `/api/knowledge-servers/${encodeURIComponent(id)}/api-key`),
  },

  // 工具
  tools: {
    list: (params?: { kind?: string; source?: string; enabled?: boolean }) =>
      request<any[]>(
        'GET',
        `/api/mcp/tools${params ? `?${new URLSearchParams(
          Object.entries(params).filter(([, v]) => v !== undefined) as any
        )}` : ''}`
      ),
    stats: () => request<any>('GET', '/api/mcp/stats'),
    enable: (toolset: string) =>
      request<any>('POST', `/api/mcp/${encodeURIComponent(toolset)}/enable`),
    disable: (toolset: string) =>
      request<any>('POST', `/api/mcp/${encodeURIComponent(toolset)}/disable`),
    approve: (toolset: string, callId: string, approved: boolean) =>
      request<any>('POST', `/api/mcp/${encodeURIComponent(toolset)}/approve`, {
        body: { call_id: callId, approved },
      }),
    connectors: () => request<any[]>('GET', '/api/mcp/connectors'),
  },

  // 审计与合规（Phase B P0）
  audit: {
    logs: (params?: { conversation_id?: string; tool_name?: string; actor?: string; limit?: number }) =>
      request<any[]>('GET', `/api/audit/logs${params ? `?${new URLSearchParams(params as any)}` : ''}`),
    replay: (conversationId: string) =>
      request<any[]>('GET', `/api/audit/replay?conversation_id=${encodeURIComponent(conversationId)}`),
    replayStats: (conversationId: string) =>
      request<any>('GET', `/api/audit/replay/stats?conversation_id=${encodeURIComponent(conversationId)}`),
    breakers: () => request<any[]>('GET', '/api/audit/breakers'),
    resetBreaker: (toolName?: string) =>
      request<any>('POST', '/api/audit/breakers/reset', {
        body: toolName ? { tool_name: toolName } : {},
      }),
  },

  // Skill 集成（Phase B P0；P2 市场/对话式）
  skills: {
    list: () => request<any[]>('GET', '/api/skills'),
    market: () => request<any>('GET', '/api/skills/market'),
    marketStats: () => request<any>('GET', '/api/skills/market/stats'),
    marketSearch: (query: string) =>
      request<any>('POST', '/api/skills/market/search', { body: { query } }),
    install: (data: { source: string; path?: string; git_url?: string; market_id?: string; query?: string }) =>
      request<any>('POST', '/api/skills/install', { body: data }),
    enable: (id: string) => request<any>('POST', `/api/skills/${encodeURIComponent(id)}/enable`),
    disable: (id: string) => request<any>('POST', `/api/skills/${encodeURIComponent(id)}/disable`),
    remove: (id: string) => request<any>('DELETE', `/api/skills/${encodeURIComponent(id)}`),
  },

  // 企业系统连接器（Phase B P1：ERP/CRM/OA）
  connectors: {
    types: () => request<any[]>('GET', '/api/connectors/types'),
    list: () => request<any[]>('GET', '/api/connectors'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/connectors', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/connectors/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/connectors/${encodeURIComponent(id)}`),
    test: (id: string) => request<any>('POST', `/api/connectors/${encodeURIComponent(id)}/test`),
    invoke: (id: string, operation: string, params: Record<string, unknown>) =>
      request<any>('POST', `/api/connectors/${encodeURIComponent(id)}/invoke`, {
        body: { operation, params },
      }),
    getApiKey: (id: string) => request<any>('GET', `/api/connectors/${encodeURIComponent(id)}/api-key`),
  },

  // 数据库只读连接（Phase B P1）
  dbConnectors: {
    list: () => request<any[]>('GET', '/api/db-connectors'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/db-connectors', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/db-connectors/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/db-connectors/${encodeURIComponent(id)}`),
    test: (id: string) => request<any>('POST', `/api/db-connectors/${encodeURIComponent(id)}/test`),
    query: (id: string, sql: string) =>
      request<any>('POST', `/api/db-connectors/${encodeURIComponent(id)}/query`, { body: { sql } }),
  },

  // Webhook 事件（Phase B P1，P3 工作流触发器）
  webhooks: {
    events: (hookId?: string, limit = 50) =>
      request<any[]>('GET', `/api/webhooks/events?${hookId ? `hook_id=${encodeURIComponent(hookId)}&` : ''}limit=${limit}`),
    send: (hookId: string, payload: Record<string, unknown>) =>
      request<any>('POST', `/api/webhooks/${encodeURIComponent(hookId)}`, { body: payload }),
  },

  // 项目空间（Phase B P2：多人协同）
  projects: {
    list: () => request<any[]>('GET', '/api/projects'),
    create: (data: { name: string; description?: string; config_share?: Record<string, unknown> }) =>
      request<any>('POST', '/api/projects', { body: data }),
    get: (id: string) => request<any>('GET', `/api/projects/${encodeURIComponent(id)}`),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/projects/${encodeURIComponent(id)}`),
    members: (id: string) => request<any[]>('GET', `/api/projects/${encodeURIComponent(id)}/members`),
    addMember: (id: string, data: { username: string; role: string }) =>
      request<any>('POST', `/api/projects/${encodeURIComponent(id)}/members`, { body: data }),
    removeMember: (id: string, username: string) =>
      request<any>('DELETE', `/api/projects/${encodeURIComponent(id)}/members/${encodeURIComponent(username)}`),
    tasks: (id: string) => request<any[]>('GET', `/api/projects/${encodeURIComponent(id)}/tasks`),
    createTask: (id: string, data: Record<string, unknown>) =>
      request<any>('POST', `/api/projects/${encodeURIComponent(id)}/tasks`, { body: data }),
    updateTask: (id: string, taskId: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`, { body: data }),
    removeTask: (id: string, taskId: string) =>
      request<any>('DELETE', `/api/projects/${encodeURIComponent(id)}/tasks/${encodeURIComponent(taskId)}`),
    assets: (id: string) => request<any[]>('GET', `/api/projects/${encodeURIComponent(id)}/assets`),
    addAsset: (id: string, data: Record<string, unknown>) =>
      request<any>('POST', `/api/projects/${encodeURIComponent(id)}/assets`, { body: data }),
    removeAsset: (id: string, assetId: string) =>
      request<any>('DELETE', `/api/projects/${encodeURIComponent(id)}/assets/${encodeURIComponent(assetId)}`),
    searchAssets: (id: string, query: string, topK = 5) =>
      request<any>('POST', `/api/projects/${encodeURIComponent(id)}/assets/search`, {
        body: { query, top_k: topK },
      }),
    configSharing: (id: string) =>
      request<any>('GET', `/api/projects/${encodeURIComponent(id)}/config-sharing`),
    setConfigSharing: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${encodeURIComponent(id)}/config-sharing`, { body: data }),
    sharedConfig: (id: string) =>
      request<any>('GET', `/api/projects/${encodeURIComponent(id)}/shared-config`),
  },

  // 自动化工作流（Phase B P3）
  workflows: {
    list: () => request<any[]>('GET', '/api/workflows'),
    templates: () => request<any[]>('GET', '/api/workflows/templates'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/workflows', { body: data }),
    get: (id: string) => request<any>('GET', `/api/workflows/${encodeURIComponent(id)}`),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/workflows/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/workflows/${encodeURIComponent(id)}`),
    run: (id: string, payload: Record<string, unknown> = {}) =>
      request<any>('POST', `/api/workflows/${encodeURIComponent(id)}/run`, { body: { payload } }),
    runs: (id: string) => request<any[]>('GET', `/api/workflows/${encodeURIComponent(id)}/runs`),
    allRuns: () => request<any[]>('GET', '/api/workflows/runs'),
  },

  // 系统运维（Phase B P2）
  ops: {
    health: () => request<any>('GET', '/api/ops/health'),
    healthCheck: () => request<any>('POST', '/api/ops/health-check'),
    diagnostics: (runId?: string) =>
      request<any>('GET', `/api/ops/diagnostics${runId ? `?run_id=${encodeURIComponent(runId)}` : ''}`),
    fieldMappings: (connectorId?: string) =>
      request<any[]>('GET', `/api/field-mappings${connectorId ? `?connector_id=${encodeURIComponent(connectorId)}` : ''}`),
    createMapping: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/field-mappings', { body: data }),
    updateMapping: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/field-mappings/${encodeURIComponent(id)}`, { body: data }),
    removeMapping: (id: string) => request<any>('DELETE', `/api/field-mappings/${encodeURIComponent(id)}`),
    exportUsage: (by: string, days = 30, fmt = 'csv') =>
      request<any>('GET', `/api/usage/export?by=${encodeURIComponent(by)}&days=${days}&fmt=${encodeURIComponent(fmt)}`),
  },

  // 企业身份与 SSO（Phase B P0）
  enterprise: {
    users: () => request<any[]>('GET', '/api/enterprise/users'),
    upsertUser: (data: { username: string; display_name?: string; role?: string; data_scope?: string; department?: string }) =>
      request<any>('POST', '/api/enterprise/users', { body: data }),
    deleteUser: (id: string) => request<any>('DELETE', `/api/enterprise/users/${encodeURIComponent(id)}`),
    me: (username?: string) =>
      request<any>('GET', `/api/enterprise/me${username ? `?username=${encodeURIComponent(username)}` : ''}`),
    ssoProviders: () => request<any>('GET', '/api/enterprise/sso/providers'),
    saveSso: (data: Record<string, unknown>) =>
      request<any>('PUT', '/api/enterprise/sso/providers', { body: data }),
    deleteSso: (name: string) => request<any>('DELETE', `/api/enterprise/sso/providers/${encodeURIComponent(name)}`),
  },

  // 设置
  settings: {
    get: () => request<any>('GET', '/api/settings'),
    update: (data: Record<string, unknown>) =>
      request<any>('PUT', '/api/settings', { body: data }),
  },

  // P1 长期记忆（可见、可删、可手动添加、可开关）
  memory: {
    list: () =>
      request<{
        memories: Array<{
          id: number
          kind: 'preference' | 'fact' | 'conclusion'
          content: string
          source_conversation?: string
          created_at: number
          updated_at: number
          hit_count: number
        }>
        total: number
        kinds: Record<string, string>
      }>('GET', '/api/memory'),
    add: (data: { kind: string; content: string }) =>
      request<{ ok: boolean; id?: number | null; dup?: boolean }>('POST', '/api/memory', {
        body: data,
      }),
    remove: (id: number) => request<{ ok: boolean }>('DELETE', `/api/memory/${id}`),
    clear: () => request<{ ok: boolean; cleared: number }>('DELETE', '/api/memory'),
  },

  // P2 主动触发：定时任务 + 通知
  schedule: {
    list: () =>
      request<{
        jobs: Array<{
          id: string
          title: string
          message: string
          model_id?: string
          schedule: Record<string, unknown>
          schedule_text: string
          enabled: boolean
          next_run_at: number | null
          last_run_at: number | null
          last_status: string
          last_task_id?: string
          conversation_id?: string
          created_at: number
          updated_at: number
        }>
      }>('GET', '/api/schedule'),
    create: (data: { title: string; message: string; schedule: Record<string, unknown>; enabled?: boolean }) =>
      request<{ ok: boolean; id: string; next_run_at: number; schedule_text: string }>(
        'POST',
        '/api/schedule',
        { body: data },
      ),
    update: (id: string, data: Record<string, unknown>) =>
      request<{ ok: boolean; id: string; schedule_text: string }>('PUT', `/api/schedule/${id}`, {
        body: data,
      }),
    remove: (id: string) => request<{ ok: boolean }>('DELETE', `/api/schedule/${id}`),
    runNow: (id: string) => request<{ ok: boolean; id: string; note?: string }>(
      'POST',
      `/api/schedule/${id}/run`,
    ),
    notifications: () =>
      request<{
        unread: number
        items: Array<{
          id: string
          job_id?: string
          title: string
          message: string
          status: string
          created_at: number
          read: number
        }>
      }>('GET', '/api/notifications'),
    markRead: () => request<{ ok: boolean }>('POST', '/api/notifications/read'),
  },

  // 用量
  usage: {
    get: (period?: string) =>
      request<any>('GET', `/api/usage${period ? `?period=${encodeURIComponent(period)}` : ''}`),
  },

  // 文件上传与解析
  files: {
    upload: (file: File, onProgress?: (percent: number) => void) =>
      uploadFile<{
        filename: string
        content_type: string
        size: number
        kind: 'text' | 'pdf' | 'docx' | 'xlsx' | 'pptx'
        extracted_text: string
        char_count: number
        truncated: boolean
        saved_path?: string
      }>('/api/files/upload', file, onProgress),
  },

  // 联网搜索服务（web_search 工具 provider 配置）
  webSearchServers: {
    list: () => request<any[]>('GET', '/api/web-search-servers'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/web-search-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/web-search-servers/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/web-search-servers/${encodeURIComponent(id)}`),
    test: (id: string) => request<any>('POST', `/api/web-search-servers/${encodeURIComponent(id)}/test`),
    getApiKey: (id: string) => request<any>('GET', `/api/web-search-servers/${encodeURIComponent(id)}/api-key`),
  },

  // 外部 MCP Server（动态挂载为 Agent 工具）
  mcpServers: {
    list: () => request<any[]>('GET', '/api/mcp-servers'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/mcp-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/mcp-servers/${encodeURIComponent(id)}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/mcp-servers/${encodeURIComponent(id)}`),
    test: (id: string) => request<any>('POST', `/api/mcp-servers/${encodeURIComponent(id)}/test`),
    sync: (id: string) => request<any>('POST', `/api/mcp-servers/${encodeURIComponent(id)}/sync`),
  },

  // P0 后台任务（长任务跑后台 + 进度查询 + 取消）
  tasks: {
    launch: (data: {
      conversation_id: string
      message: string
      model_id?: string
      mode?: string
      workspace_dir?: string
      /** 首次启动落库用户消息；续跑（重试）传 false 避免重复入史 */
      persist_user?: boolean
    }) => request<{ ok: boolean; task_id: string }>('POST', '/api/tasks/launch', { body: data }),
    get: (taskId: string) =>
      request<{
        task_id: string
        status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
        conversation_id: string
        model_id?: string
        error?: string | null
        events: Array<Record<string, unknown>>
        created_at: number
        updated_at: number
      }>('GET', `/api/tasks/${encodeURIComponent(taskId)}`),
    cancel: (taskId: string) =>
      request<{ ok: boolean; task_id: string; status: string; note?: string }>(
        'POST',
        `/api/tasks/${encodeURIComponent(taskId)}/cancel`
      ),
  },

  // P0.6 语音交互：TTS 朗读 + ASR 语音输入（后端全本地：say + whisper）
  speech: {
    /** 语音转文本：传录音 Blob（webm/wav…）→ {text} */
    transcribe: async (blob: Blob): Promise<{ text: string; language?: string }> => {
      const token = (await window.electronAPI?.getAgentToken()) || ''
      const formData = new FormData()
      formData.append('file', blob, 'recording.webm')
      const resp = await fetch(`${apiBase}/api/speech/transcribe`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      })
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        throw new Error(`HTTP ${resp.status}: ${text}`)
      }
      return resp.json() as Promise<{ text: string; language?: string }>
    },
    /** 文本转语音：{text} → {path, url}（url 为 /api/speech/audio/xx.wav） */
    synthesize: (text: string) =>
      request<{ path: string; url: string; voice: string }>('POST', '/api/speech/synthesize', {
        body: { text },
      }),
  },
}
