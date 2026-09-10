import { spawn, ChildProcess } from 'child_process'
import { join } from 'path'
import { existsSync } from 'fs'
import { randomBytes } from 'crypto'
import { app } from 'electron'

let agentProcess: ChildProcess | null = null
const MAX_RESTARTS = 3
let restartCount = 0
let stopped = false
// 审批服务 URL/共享密钥在整个 app 生命周期内不变（由 main.ts 启动一次）。
// 模块级记住它们：崩溃自动重启走 spawnAgentProcess(token) 不带后续参数，
// 若不记忆，重启后的后端拿不到 AGENT_APPROVAL_URL/SECRET，shell 审批会全部失效。
let currentApprovalUrl = ''
let currentApprovalSecret = ''

export const AGENT_PORT = Number(process.env.AGENT_PORT || 8765)

/** 打包态（DMG/.app）与开发态（npx electron .）路径模式切换 */
const isPackaged = app.isPackaged

/**
 * 生成一次性随机 Token（32 字节 = 64 位十六进制）。
 * 每次启动都重新生成，不落盘、不复用 —— 只对本次进程生命周期内的本地回环请求有效。
 */
export function getAgentToken(): string {
  return randomBytes(32).toString('hex')
}

/**
 * 解析 Python 解释器：
 *   1) 显式配置 AGENT_PYTHON
 *   2) 打包态内置 python-build-standalone（runtime/python/<key>/...）—— 跨机器可移植
 *   3) 同机 venv（agent/.venv/bin/python，遗留兼容）
 *   4) 系统 python3
 */
function resolvePython(): string {
  if (process.env.AGENT_PYTHON) return process.env.AGENT_PYTHON

  if (isPackaged) {
    const base = join(process.resourcesPath, 'runtime', 'python')
    if (existsSync(base)) {
      // 顺序：先按 process.platform 找对应子目录，找不到则取第一个存在的 key
      const want = process.platform === 'win32' ? 'win-x64' : `${process.platform}-${process.arch}`
      const preferKeys = [want, ...(want !== 'macos-arm64' ? ['macos-arm64'] : []), ...(want !== 'macos-x64' ? ['macos-x64'] : []), ...(want !== 'win-x64' ? ['win-x64'] : [])]
      for (const k of preferKeys) {
        const exe = process.platform === 'win32'
          ? join(base, k, 'python.exe')
          : join(base, k, 'bin', 'python3')
        if (existsSync(exe)) return exe
      }
    }
    // 兼容旧版内置 venv
    const legacy = join(process.resourcesPath, 'agent/.venv/bin/python')
    if (existsSync(legacy)) return legacy
    return 'python3'
  }

  // 开发态：dist-electron/../../runtime/python/<host-key>/...（可选）
  const devRt = join(__dirname, '..', '..', 'runtime', 'python')
  if (existsSync(devRt)) {
    const want = `${process.platform}-${process.arch === 'arm64' || process.arch === 'aarch64' ? 'arm64' : 'x64'}`
    const exe = process.platform === 'win32'
      ? join(devRt, want, 'python.exe')
      : join(devRt, want, 'bin', 'python3')
    if (existsSync(exe)) return exe
  }
  // 开发态 venv
  const devPy = join(__dirname, '..', '..', 'agent', '.venv', 'bin', 'python')
  if (existsSync(devPy)) return devPy
  return 'python3'
}

/** 解析 agent 根目录：打包态在 Resources/agent，开发态在仓库 agent/ */
function resolveAgentRoot(): string {
  if (isPackaged) return join(process.resourcesPath, 'agent')
  return join(__dirname, '..', '..', 'agent')
}

/** 打包态数据目录必须可写：落在 userData（~Library/Application Support/...） */
function resolveDataDir(): string {
  if (isPackaged) return join(app.getPath('userData'), 'agent-data')
  return ''
}

/**
 * 可写配置目录：打包态 .app/Contents/Resources 对用户只读，
 * 直接写会 PermissionError，因此配置必须落在 userData。
 * 首次启动时后端会自动把包内 config/settings.json 复制过来作为初始值。
 */
function resolveConfigDir(): string {
  if (isPackaged) return join(app.getPath('userData'), 'config')
  return ''
}

export async function spawnAgentProcess(
  token: string,
  approvalUrl?: string,
  approvalSecret?: string,
): Promise<boolean> {
  if (agentProcess) stopAgentProcess()

  stopped = false
  // 显式传入则更新（首次启动）；未传（崩溃自动重启）则复用上一次的值
  if (approvalUrl) currentApprovalUrl = approvalUrl
  if (approvalSecret) currentApprovalSecret = approvalSecret
  const python = resolvePython()
  const agentRoot = resolveAgentRoot()

  // main.py 使用相对导入（from .api import ...），必须以包方式运行：
  // python -m app.main（cwd=agent 根目录），直接跑脚本会 ImportError。
  console.log(`[Agent] 启动: ${python} -m app.main (cwd=${agentRoot}, port ${AGENT_PORT})`)

  agentProcess = spawn(python, ['-m', 'app.main'], {
    cwd: agentRoot,
    env: {
      ...process.env,
      AGENT_TOKEN: token,
      AGENT_APPROVAL_URL: currentApprovalUrl,
      // 审批服务共享密钥：后端回调 /approve 时必须携带，
      // 防止本机其他进程扫描到端口后伪造批准请求
      AGENT_APPROVAL_SECRET: currentApprovalSecret,
      AGENT_PORT: String(AGENT_PORT),
      AGENT_HOST: '127.0.0.1',
      PYTHONUNBUFFERED: '1',
      // 打包态：数据（SQLite/附件）写入 userData，避免写入只读的 .app 资源目录
      ...(resolveDataDir() ? { AGENT_DATA_DIR: resolveDataDir() } : {}),
      // 打包态：用户配置（settings.json）写入 userData，Resources/config 仅作出厂默认值
      ...(resolveConfigDir() ? { AGENT_CONFIG_DIR: resolveConfigDir() } : {}),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  agentProcess.stdout?.on('data', (data: Buffer) => {
    process.stdout.write(`[Agent] ${data}`)
  })

  agentProcess.stderr?.on('data', (data: Buffer) => {
    process.stderr.write(`[Agent:err] ${data}`)
  })

  agentProcess.on('error', (err: Error) => {
    console.error('[Agent] 进程启动失败:', err.message)
  })

  agentProcess.on('close', (code: number | null) => {
    console.log(`[Agent] 进程退出，code=${code}`)
    agentProcess = null
    if (stopped) return
    if (code !== 0 && restartCount < MAX_RESTARTS) {
      restartCount++
      console.log(`[Agent] 自动重启 (${restartCount}/${MAX_RESTARTS})...`)
      // 不传 approvalUrl：spawnAgentProcess 内部复用 currentApprovalUrl，
      // 保证重启后的后端仍能连上 shell 审批服务
      setTimeout(() => { void spawnAgentProcess(token) }, 2000)
    }
  })

  return waitForHealthz(AGENT_PORT, 30_000)
}

/**
 * 轮询 /healthz 等待后端就绪。
 * 注意：返回 false 而非 reject —— 主窗口不应被后端启动失败阻塞，
 * 渲染层会自行轮询并展示可重试的错误页。
 */
export function waitForHealthz(
  port: number,
  timeout = 30_000,
  interval = 500
): Promise<boolean> {
  return new Promise((resolve) => {
    const start = Date.now()

    const check = async () => {
      if (stopped) return resolve(false)
      try {
        const resp = await fetch(`http://127.0.0.1:${port}/healthz`)
        if (resp.ok) {
          console.log('[Agent] 健康检查通过')
          return resolve(true)
        }
      } catch {
        // 服务尚未监听，继续重试
      }

      if (Date.now() - start > timeout) {
        console.error('[Agent] 健康检查超时')
        return resolve(false)
      }
      setTimeout(check, interval)
    }

    check()
  })
}

export function stopAgentProcess(): void {
  stopped = true
  restartCount = 0
  if (agentProcess) {
    agentProcess.kill('SIGTERM')
    agentProcess = null
  }
}

export function isAgentRunning(): boolean {
  return agentProcess !== null
}
