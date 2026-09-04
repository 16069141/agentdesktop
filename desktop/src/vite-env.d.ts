/// <reference types="vite/client" />

interface ElectronAPI {
  getAgentToken: () => Promise<string>
  getAgentStatus: () => Promise<{ isRunning: boolean }>
  minimizeWindow: () => void
  maximizeWindow: () => void
  closeWindow: () => void
  openExternal: (url: string) => void
  openFileDialog: (options: Electron.OpenDialogOptions) => Promise<string[] | null>
  requestShellApproval: (command: string, args: string[]) => Promise<boolean>
}

declare global {
  interface Window {
    electronAPI: ElectronAPI
  }
}

export {}
