import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('electronAPI', {
  getAgentToken: () => ipcRenderer.invoke('get-agent-token'),
  getAgentStatus: () => ipcRenderer.invoke('agent-status'),
  minimizeWindow: () => ipcRenderer.send('window-minimize'),
  maximizeWindow: () => ipcRenderer.send('window-maximize'),
  closeWindow: () => ipcRenderer.send('window-close'),
  openExternal: (url: string) => ipcRenderer.send('open-external', url),
  openFileDialog: (options: Electron.OpenDialogOptions) =>
    ipcRenderer.invoke('open-file-dialog', options),
  requestShellApproval: (command: string, args: string[]) =>
    ipcRenderer.invoke('shell-approval', command, args),
})
