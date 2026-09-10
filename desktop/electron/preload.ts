import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  getAgentToken: () => ipcRenderer.invoke('get-agent-token'),
  getAgentStatus: () => ipcRenderer.invoke('agent-status'),
  onAgentReady: (callback: () => void) => ipcRenderer.on('agent-ready', callback),
  minimizeWindow: () => ipcRenderer.send('window-minimize'),
  maximizeWindow: () => ipcRenderer.send('window-maximize'),
  closeWindow: () => ipcRenderer.send('window-close'),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),
  openFileDialog: (options: Electron.OpenDialogOptions) =>
    ipcRenderer.invoke('open-file-dialog', options),
  saveFile: (payload: { defaultPath: string; content: string }) =>
    ipcRenderer.invoke('save-file', payload),
  requestShellApproval: (command: string, args: string[]) =>
    ipcRenderer.invoke('shell-approval', command, args),
  revealInFolder: (path: string) => ipcRenderer.invoke('reveal-in-folder', path),
  openFile: (path: string) => ipcRenderer.invoke('open-file', path),
})
