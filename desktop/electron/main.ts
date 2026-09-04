import { app, BrowserWindow, ipcMain, shell, dialog } from 'electron'
import path from 'path'
import { fileURLToPath } from 'url'
import {
  spawnAgentProcess,
  stopAgentProcess,
  getAgentToken,
  isAgentRunning,
} from './agentProcess'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

let mainWindow: BrowserWindow | null = null
let agentToken = ''

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    frame: false,
    backgroundColor: '#0A0F1A',
    title: 'PrivateAI',
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
// Phase 2：Shell 命令执行需经此通道弹出二次确认
ipcMain.handle('shell-approval', async (_event, command: string) => {
  if (!mainWindow) return { approved: false }
  const res = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    buttons: ['拒绝', '允许执行'],
    defaultId: 0,
    cancelId: 0,
    title: '命令执行确认',
    message: 'AI 请求执行本地命令',
    detail: `命令：${command}\n\n仅在你确认该命令安全后允许执行。`,
  })
  return { approved: res.response === 1 }
})

app.whenReady().then(async () => {
  agentToken = getAgentToken()

  // 先建窗口，再拉起后端：后端启动慢也不会白屏，
  // 渲染层会轮询 /healthz 并展示启动进度。
  createWindow()

  const ok = await spawnAgentProcess(agentToken)
  if (!ok) {
    console.error('[Agent] 后端启动失败，渲染层将展示错误提示')
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
})
