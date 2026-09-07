/// <reference types="vite/client" />

interface ElectronAPI {
  getAgentToken: () => Promise<string>
  getAgentStatus: () => Promise<{ isRunning: boolean }>
  onAgentReady: (callback: () => void) => void
  minimizeWindow: () => void
  maximizeWindow: () => void
  closeWindow: () => void
  openExternal: (url: string) => void
  openFileDialog: (options: Electron.OpenDialogOptions) => Promise<string[] | null>
  saveFile: (payload: { defaultPath: string; content: string }) => Promise<{ ok: boolean; path?: string; canceled?: boolean; error?: string }>
  requestShellApproval: (command: string, args: string[]) => Promise<boolean>
  revealInFolder: (path: string) => Promise<{ ok: boolean; error?: string }>
  openFile: (path: string) => Promise<{ ok: boolean; error?: string }>
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export {}
