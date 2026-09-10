import { execSync } from 'child_process'
import { randomBytes } from 'crypto'

// 一次性随机 token（每次启动生成，存钥匙串）
export function getAgentToken(): string {
  // TODO: Phase 2 接入系统钥匙串
  // 当前返回随机 token，实际应检查钥匙串是否已有，有则复用
  try {
    const stored = execSync('security find-generic-password -s "private-ai-agent-token" -w 2>/dev/null', {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'ignore'],
    }).trim()
    if (stored) return stored
  } catch {
    // 钥匙串中没有，生成新的
  }
  const token = randomBytes(32).toString('hex')
  try {
    execSync(`security add-generic-password -s "private-ai-agent-token" -w "${token}"`, {
      stdio: 'ignore',
    })
  } catch {
    // macOS 权限问题，降级使用内存 token
  }
  return token
}

export function getKeychainValue(key: string): string | null {
  try {
    const value = execSync(`security find-generic-password -s "${key}" -w 2>/dev/null`, {
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'ignore'],
    }).trim()
    return value || null
  } catch {
    return null
  }
}

export function storeKeychainValue(key: string, value: string): void {
  try {
    execSync(`security add-generic-password -s "${key}" -w "${value}"`, {
      stdio: 'ignore',
    })
  } catch (e) {
    console.error('Keychain store failed:', e)
  }
}
