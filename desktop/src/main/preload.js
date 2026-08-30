// Preload 脚本 — 安全桥接主进程与渲染进程
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('localflow', {
  getAppInfo: () => ipcRenderer.invoke('app:get-info'),
  getApiBase: () => ipcRenderer.invoke('app:api-base'),
  getDshBase: () => ipcRenderer.invoke('app:dsh-base'),
  pickDirectory: () => ipcRenderer.invoke('app:pick-directory'),
  onDshStatus: (cb) => ipcRenderer.on('dsh-status', (_e, up) => cb(up)),
});