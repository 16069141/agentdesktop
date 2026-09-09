import { app, BrowserWindow, ipcMain, shell, dialog } from 'electron'
import path from 'path'
import fs from 'fs'
import crypto from 'crypto'
import { fileURLToPath } from 'url'
import { createServer } from 'http'
import type { Server } from 'http'
import {
  spawnAgentProcess,
  stopAgentProcess,
  getAgentToken,
  isAgentRunning,
} from './agentProcess'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

let mainWindow: BrowserWindow | null = null
let agentToken = ''
let approvalServer: Server | null = null
// 审批服务共享密钥：每次启动随机生成，仅注入后端进程环境变量。
// 本机其他进程即使扫描到审批端口，无此密钥也无法伪造批准请求。
let approvalSecret = ''

/** 弹出「命令执行确认」系统对话框；窗口不存在或用户拒绝时返回 false（安全优先） */
async function requestShellApproval(command: string): Promise<boolean> {
  if (!mainWindow) return false
  const res = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['拒绝', '允许执行'],
    defaultId: 0,
    cancelId: 0,
    title: '命令执行确认',
    message: 'AI 请求执行本地命令',
    detail: `命令：${command}\n\n仅在你确认该命令安全后允许执行。`,
  })
  return res.response === 1
}

/** 常量时间比较，避免密钥校验的时序侧信道 */
function secretMatches(provided: string | undefined, expected: string): boolean {
  if (!provided || !expected) return false
  const a = Buffer.from(provided)
  const b = Buffer.from(expected)
  if (a.length !== b.length) return false
  return crypto.timingSafeEqual(a, b)
}

/**
 * 启动本机审批服务（仅监听 127.0.0.1 随机端口）。
 * 后端 Agent 在执行 shell 命令前通过 HTTP 调用此服务弹出 UI 确认，
 * 从而打通「Shell 白名单 + UI 确认 + 执行」的完整闭环。
 * 每个 /approve 请求必须携带 X-Approval-Secret 头（值为启动时生成的
 * 一次性密钥），否则直接拒绝——防止本机其他进程扫描端口后伪造批准。
 * 返回可被 Agent 使用的审批 URL。
 */
function startApprovalServer(secret: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const server = createServer((req, res) => {
      if (req.method !== 'POST' || req.url !== '/approve') {
        res.writeHead(404, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ approved: false }))
        return
      }
      // 共享密钥校验：无密钥或密钥错误一律拒绝（安全优先）
      if (!secretMatches(req.headers['x-approval-secret'] as string | undefined, secret)) {
        res.writeHead(401, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ approved: false, error: 'unauthorized' }))
        return
      }
      let body = ''
      req.on('data', (chunk) => (body += chunk))
      req.on('end', async () => {
        let command = ''
        try {
          command = (JSON.parse(body) as { command?: string }).command ?? ''
        } catch {
          // 忽略非法 JSON，按拒绝处理
        }
        const approved = await requestShellApproval(String(command))
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ approved }))
      })
    })
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const addr = server.address()
      const port = typeof addr === 'object' && addr ? addr.port : 0
      approvalServer = server
      console.log(`[Agent] 审批服务已就绪: http://127.0.0.1:${port}`)
      resolve(`http://127.0.0.1:${port}`)
    })
  })
}

function stopApprovalServer(): void {
  if (approvalServer) {
    approvalServer.close()
    approvalServer = null
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    frame: false,
    backgroundColor: '#0A0F1A',
    title: '颤翎子',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
    mainWindow.webContents.openDevTools()
  } else {
    mainWindow.loadFile(path.join(__dirname, '../dist/index.html'))
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

/* ---------------- IPC 白名单 ----------------
 * 渲染进程只能通过下面这组固定通道通信，杜绝任意 Node 能力暴露。
 */
ipcMain.handle('get-agent-token', () => agentToken)
ipcMain.handle('agent-status', () => ({ isRunning: isAgentRunning() }))
ipcMain.on('window-minimize', () => mainWindow?.minimize())
ipcMain.on('window-maximize', () => {
  if (!mainWindow) return
  mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize()
})
ipcMain.on('window-close', () => mainWindow?.close())
ipcMain.on('open-external', (_event, url: string) => {
  if (typeof url === 'string' && /^https?:\/\//.test(url)) {
    shell.openExternal(url)
  }
})
ipcMain.handle('open-file-dialog', async (_event, options) => {
  const result = await dialog.showOpenDialog(mainWindow!, options)
  return result.canceled ? null : result.filePaths
})
// 会话导出：原生「保存」对话框写入文件
ipcMain.handle('save-file', async (_event, payload: { defaultPath: string; content: string }) => {
  const { defaultPath, content } = payload || {}
  if (typeof content !== 'string') return { ok: false, error: 'content 缺失' }
  const result = await dialog.showSaveDialog(mainWindow!, {
    defaultPath: defaultPath || '对话导出.md',
    filters: [
      { name: 'Markdown', extensions: ['md'] },
      { name: '文本文件', extensions: ['txt'] },
    ],
  })
  if (result.canceled || !result.filePath) return { ok: false, canceled: true }
  try {
    await fs.promises.writeFile(result.filePath, content, 'utf-8')
    return { ok: true, path: result.filePath }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
})
// Phase 2：Shell 命令执行需经此通道弹出二次确认
ipcMain.handle('shell-approval', async (_event, command: string) => {
  return { approved: await requestShellApproval(String(command)) }
})
// Agent 交付文件：在 Finder 中显示文件 / 打开文件
ipcMain.handle('reveal-in-folder', async (_event, path: string) => {
  if (typeof path !== 'string' || !path.startsWith('/')) {
    return { ok: false, error: '非法路径' }
  }
  try {
    shell.showItemInFolder(path)
    return { ok: true }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
})
ipcMain.handle('open-file', async (_event, path: string) => {
  if (typeof path !== 'string' || !path.startsWith('/')) {
    return { ok: false, error: '非法路径' }
  }
  try {
    const err = await shell.openPath(path)
    return err ? { ok: false, error: err } : { ok: true }
  } catch (err) {
    return { ok: false, error: String(err) }
  }
})

app.whenReady().then(async () => {
  agentToken = getAgentToken()

  // 先建窗口，再拉起后端：后端启动慢也不会白屏，
  // 渲染层会轮询 /healthz 并展示启动进度。
  createWindow()

  // 启动审批服务，并把地址 + 一次性密钥注入 Agent，打通 shell 命令 UI 确认闭环。
  // 密钥每次启动随机生成，仅通过环境变量传给后端，不落盘、不入前端。
  let approvalUrl = ''
  approvalSecret = crypto.randomBytes(32).toString('hex')
  try {
    approvalUrl = await startApprovalServer(approvalSecret)
  } catch (err) {
    console.error('[Agent] 审批服务启动失败，shell 命令将一律拒绝:', err)
  }

  const ok = await spawnAgentProcess(agentToken, approvalUrl, approvalSecret)
  if (!ok) {
    console.error('[Agent] 后端启动失败，渲染层将展示错误提示')
  } else {
    // 后端就绪后主动通知前端，免去渲染层轮询 /healthz 的等待
    mainWindow?.webContents.send('agent-ready')
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  stopAgentProcess()
  stopApprovalServer()
})
