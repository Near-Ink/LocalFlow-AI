// Preload 脚本 — 安全桥接主进程与渲染进程
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('localflow', {
  getAppInfo: () => ipcRenderer.invoke('app:get-info'),
  getApiBase: () => ipcRenderer.invoke('app:api-base'),
  pickDirectory: () => ipcRenderer.invoke('app:pick-directory'),
});