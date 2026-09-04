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
    export: (id: string, format: 'md' | 'json') =>
      request<string>('POST', `/api/conversations/${id}/export?format=${format}`),
  },

  // 模型
  models: {
    list: () => request<any[]>('GET', '/api/models'),
  },

  // 知识库
  knowledge: {
    ingest: (formData: FormData) => {
      const token = window.electronAPI?.getAgentToken() || ''
      return fetch(`${apiBase}/api/knowledge/ingest`, {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: formData,
      }).then((r) => r.json())
    },
    search: (query: string, topK = 5) =>
      request<any[]>('POST', '/api/knowledge/search', {
        body: { query, top_k: topK },
      }),
    delete: (docId: string) =>
      request<any>('DELETE', `/api/knowledge/${docId}`),
    listDocs: () => request<any[]>('GET', '/api/knowledge/docs'),
  },

  // 工具
  tools: {
    list: () => request<any[]>('GET', '/api/mcp/tools'),
    enable: (toolset: string) =>
      request<any>('POST', `/api/mcp/${toolset}/enable`),
    disable: (toolset: string) =>
      request<any>('POST', `/api/mcp/${toolset}/disable`),
    approve: (toolset: string, callId: string, approved: boolean) =>
      request<any>('POST', `/api/mcp/${toolset}/approve`, {
        body: { call_id: callId, approved },
      }),
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
}
