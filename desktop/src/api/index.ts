export const apiBase = 'http://127.0.0.1:8765'

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
    list: () => request<any[]>('GET', '/api/conversations'),
    create: (data: { title: string; modelId: string }) =>
      request<any>('POST', '/api/conversations', { body: data }),
    get: (id: string) => request<any>('GET', `/api/conversations/${id}`),
    delete: (id: string) => request<any>('DELETE', `/api/conversations/${id}`),
    update: (id: string, data: { title?: string; modelId?: string }) =>
      request<any>('PATCH', `/api/conversations/${id}`, { body: data }),
    generateTitle: (id: string, message: string, modelId?: string) =>
      request<any>('POST', `/api/conversations/${id}/generate-title`, { body: { message, modelId } }),
    export: (id: string, format: 'md' | 'json') =>
      request<string>('POST', `/api/conversations/${id}/export?format=${format}`),
  },

  // 模型
  models: {
    list: () => request<any[]>('GET', '/api/models'),
  },

  // 模型服务器连接（局域网/互联网大模型服务器）
  llmServers: {
    list: () => request<any[]>('GET', '/api/llm-servers'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/llm-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/llm-servers/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/llm-servers/${id}`),
    test: (id: string) => request<any>('POST', `/api/llm-servers/${id}/test`),
    syncModels: (id: string) =>
      request<any>('POST', `/api/llm-servers/${id}/sync-models`),
    models: (id: string) => request<any>('GET', `/api/llm-servers/${id}/models`),
    getApiKey: (id: string) => request<any>('GET', `/api/llm-servers/${id}/api-key`),
  },

  // 知识库连接（局域网/互联网 RAG、LLM Wiki 等）
  knowledgeServers: {
    list: () => request<any[]>('GET', '/api/knowledge-servers'),
    platforms: () => request<any[]>('GET', '/api/knowledge-servers/platforms'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/knowledge-servers', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/knowledge-servers/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/knowledge-servers/${id}`),
    test: (id: string) => request<any>('POST', `/api/knowledge-servers/${id}/test`),
    search: (id: string, query: string, topK = 5) =>
      request<any>('POST', `/api/knowledge-servers/${id}/search`, {
        body: { query, top_k: topK },
      }),
    spaces: (id: string) => request<any>('GET', `/api/knowledge-servers/${id}/spaces`),
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
      request<any>('POST', `/api/mcp/${toolset}/enable`),
    disable: (toolset: string) =>
      request<any>('POST', `/api/mcp/${toolset}/disable`),
    approve: (toolset: string, callId: string, approved: boolean) =>
      request<any>('POST', `/api/mcp/${toolset}/approve`, {
        body: { call_id: callId, approved },
      }),
    connectors: () => request<any[]>('GET', '/api/mcp/connectors'),
  },

  // 审计与合规（Phase B P0）
  audit: {
    logs: (params?: { conversation_id?: string; tool_name?: string; actor?: string; limit?: number }) =>
      request<any[]>('GET', `/api/audit/logs${params ? `?${new URLSearchParams(params as any)}` : ''}`),
    replay: (conversationId: string) =>
      request<any[]>('GET', `/api/audit/replay?conversation_id=${conversationId}`),
    replayStats: (conversationId: string) =>
      request<any>('GET', `/api/audit/replay/stats?conversation_id=${conversationId}`),
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
    enable: (id: string) => request<any>('POST', `/api/skills/${id}/enable`),
    disable: (id: string) => request<any>('POST', `/api/skills/${id}/disable`),
    remove: (id: string) => request<any>('DELETE', `/api/skills/${id}`),
  },

  // 企业系统连接器（Phase B P1：ERP/CRM/OA）
  connectors: {
    types: () => request<any[]>('GET', '/api/connectors/types'),
    list: () => request<any[]>('GET', '/api/connectors'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/connectors', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/connectors/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/connectors/${id}`),
    test: (id: string) => request<any>('POST', `/api/connectors/${id}/test`),
    invoke: (id: string, operation: string, params: Record<string, unknown>) =>
      request<any>('POST', `/api/connectors/${id}/invoke`, {
        body: { operation, params },
      }),
  },

  // 数据库只读连接（Phase B P1）
  dbConnectors: {
    list: () => request<any[]>('GET', '/api/db-connectors'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/db-connectors', { body: data }),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/db-connectors/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/db-connectors/${id}`),
    test: (id: string) => request<any>('POST', `/api/db-connectors/${id}/test`),
    query: (id: string, sql: string) =>
      request<any>('POST', `/api/db-connectors/${id}/query`, { body: { sql } }),
  },

  // Webhook 事件（Phase B P1，P3 工作流触发器）
  webhooks: {
    events: (hookId?: string, limit = 50) =>
      request<any[]>('GET', `/api/webhooks/events${hookId ? `?hook_id=${hookId}` : ''}&limit=${limit}`),
    send: (hookId: string, payload: Record<string, unknown>) =>
      request<any>('POST', `/api/webhooks/${hookId}`, { body: payload }),
  },

  // 项目空间（Phase B P2：多人协同）
  projects: {
    list: () => request<any[]>('GET', '/api/projects'),
    create: (data: { name: string; description?: string; config_share?: Record<string, unknown> }) =>
      request<any>('POST', '/api/projects', { body: data }),
    get: (id: string) => request<any>('GET', `/api/projects/${id}`),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/projects/${id}`),
    members: (id: string) => request<any[]>('GET', `/api/projects/${id}/members`),
    addMember: (id: string, data: { username: string; role: string }) =>
      request<any>('POST', `/api/projects/${id}/members`, { body: data }),
    removeMember: (id: string, username: string) =>
      request<any>('DELETE', `/api/projects/${id}/members/${username}`),
    tasks: (id: string) => request<any[]>('GET', `/api/projects/${id}/tasks`),
    createTask: (id: string, data: Record<string, unknown>) =>
      request<any>('POST', `/api/projects/${id}/tasks`, { body: data }),
    updateTask: (id: string, taskId: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${id}/tasks/${taskId}`, { body: data }),
    removeTask: (id: string, taskId: string) =>
      request<any>('DELETE', `/api/projects/${id}/tasks/${taskId}`),
    assets: (id: string) => request<any[]>('GET', `/api/projects/${id}/assets`),
    addAsset: (id: string, data: Record<string, unknown>) =>
      request<any>('POST', `/api/projects/${id}/assets`, { body: data }),
    removeAsset: (id: string, assetId: string) =>
      request<any>('DELETE', `/api/projects/${id}/assets/${assetId}`),
    searchAssets: (id: string, query: string, topK = 5) =>
      request<any>('POST', `/api/projects/${id}/assets/search`, {
        body: { query, top_k: topK },
      }),
    configSharing: (id: string) =>
      request<any>('GET', `/api/projects/${id}/config-sharing`),
    setConfigSharing: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/projects/${id}/config-sharing`, { body: data }),
    sharedConfig: (id: string) =>
      request<any>('GET', `/api/projects/${id}/shared-config`),
  },

  // 自动化工作流（Phase B P3）
  workflows: {
    list: () => request<any[]>('GET', '/api/workflows'),
    templates: () => request<any[]>('GET', '/api/workflows/templates'),
    create: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/workflows', { body: data }),
    get: (id: string) => request<any>('GET', `/api/workflows/${id}`),
    update: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/workflows/${id}`, { body: data }),
    remove: (id: string) => request<any>('DELETE', `/api/workflows/${id}`),
    run: (id: string, payload: Record<string, unknown> = {}) =>
      request<any>('POST', `/api/workflows/${id}/run`, { body: { payload } }),
    runs: (id: string) => request<any[]>('GET', `/api/workflows/${id}/runs`),
    allRuns: () => request<any[]>('GET', '/api/workflows/runs'),
  },

  // 系统运维（Phase B P2）
  ops: {
    health: () => request<any>('GET', '/api/ops/health'),
    healthCheck: () => request<any>('POST', '/api/ops/health-check'),
    diagnostics: (runId?: string) =>
      request<any>('GET', `/api/ops/diagnostics${runId ? `?run_id=${runId}` : ''}`),
    fieldMappings: (connectorId?: string) =>
      request<any[]>('GET', `/api/field-mappings${connectorId ? `?connector_id=${connectorId}` : ''}`),
    createMapping: (data: Record<string, unknown>) =>
      request<any>('POST', '/api/field-mappings', { body: data }),
    updateMapping: (id: string, data: Record<string, unknown>) =>
      request<any>('PUT', `/api/field-mappings/${id}`, { body: data }),
    removeMapping: (id: string) => request<any>('DELETE', `/api/field-mappings/${id}`),
    exportUsage: (by: string, days = 30, fmt = 'csv') =>
      request<any>('GET', `/api/usage/export?by=${by}&days=${days}&fmt=${fmt}`),
  },

  // 企业身份与 SSO（Phase B P0）
  enterprise: {
    users: () => request<any[]>('GET', '/api/enterprise/users'),
    upsertUser: (data: { username: string; display_name?: string; role?: string; data_scope?: string; department?: string }) =>
      request<any>('POST', '/api/enterprise/users', { body: data }),
    deleteUser: (id: string) => request<any>('DELETE', `/api/enterprise/users/${id}`),
    me: (username?: string) =>
      request<any>('GET', `/api/enterprise/me${username ? `?username=${username}` : ''}`),
    ssoProviders: () => request<any>('GET', '/api/enterprise/sso/providers'),
    saveSso: (data: Record<string, unknown>) =>
      request<any>('PUT', '/api/enterprise/sso/providers', { body: data }),
    deleteSso: (name: string) => request<any>('DELETE', `/api/enterprise/sso/providers/${name}`),
  },

  // 设置
  settings: {
    get: () => request<any>('GET', '/api/settings'),
    update: (data: Record<string, unknown>) =>
      request<any>('PUT', '/api/settings', { body: data }),
  },

  // 用量
  usage: {
    get: (period?: string) =>
      request<any>('GET', `/api/usage${period ? `?period=${period}` : ''}`),
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
}
