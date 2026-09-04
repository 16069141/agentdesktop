import { spawn, ChildProcess } from 'child_process'
import { join } from 'path'
import { randomBytes } from 'crypto'

let agentProcess: ChildProcess | null = null
const MAX_RESTARTS = 3
let restartCount = 0
let stopped = false

export const AGENT_PORT = Number(process.env.AGENT_PORT || 8765)

/**
 * 生成一次性随机 Token（32 字节 = 64 位十六进制）。
 * 每次启动都重新生成，不落盘、不复用 —— 只对本次进程生命周期内的本地回环请求有效。
 */
export function getAgentToken(): string {
  return randomBytes(32).toString('hex')
}

/** 解析 Python 解释器：优先显式配置，其次打包后的内置 venv，最后回落到 python3 */
function resolvePython(): string {
  if (process.env.AGENT_PYTHON) return process.env.AGENT_PYTHON

  // 生产包：resources/agent/.venv/bin/python
  const bundled = join(__dirname, '../../agent/.venv/bin/python')
  return process.env.NODE_ENV === 'development' ? 'python3' : bundled
}

/** 解析 agent 入口：dist-electron/../../agent/app/main.py */
function resolveAgentScript(): string {
  return join(__dirname, '..', '..', 'agent', 'app', 'main.py')
}

export async function spawnAgentProcess(token: string): Promise<boolean> {
  if (agentProcess) stopAgentProcess()

  stopped = false
  const python = resolvePython()
  const script = resolveAgentScript()

  console.log(`[Agent] 启动: ${python} ${script} (port ${AGENT_PORT})`)

  agentProcess = spawn(python, [script], {
    env: {
      ...process.env,
      AGENT_TOKEN: token,
      AGENT_PORT: String(AGENT_PORT),
      AGENT_HOST: '127.0.0.1',
      PYTHONUNBUFFERED: '1',
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
      setTimeout(() => spawnAgentProcess(token), 2000)
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
