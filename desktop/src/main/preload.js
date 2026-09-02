// Preload 脚本 — 安全桥接主进程与渲染进程
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('localflow', {
  getAppInfo: () => ipcRenderer.invoke('app:get-info'),
  getApiBase: () => ipcRenderer.invoke('app:api-base'),
  getDshBase: () => ipcRenderer.invoke('app:dsh-base'),
  pickDirectory: () => ipcRenderer.invoke('app:pick-directory'),
  onDshStatus: (cb) => ipcRenderer.on('dsh-status', (_e, up) => cb(up)),
  // 对话引擎（dsh）内置自安装 / 更新进度
  onDshInstall: (cb) => ipcRenderer.on('dsh-install', (_e, s) => cb(s)),
  getDshInstallState: () => ipcRenderer.invoke('app:dsh-install-state'),
  retryDshInstall: () => ipcRenderer.invoke('app:dsh-install-retry'),
  // 对话引擎启动诊断（崩溃时回传 exitCode / stderr / 命令 / 日志路径）
  onDshLaunchError: (cb) => ipcRenderer.on('dsh-launch-error', (_e, s) => cb(s)),
  getDshLaunchErrorState: () => ipcRenderer.invoke('app:dsh-launch-error-state'),
  relaunchDsh: () => ipcRenderer.invoke('app:dsh-relaunch'),
});