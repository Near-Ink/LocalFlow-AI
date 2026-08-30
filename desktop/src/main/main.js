// Electron 主进程
// 负责：窗口管理、后端进程启动（打包后自动拉起内嵌 Python 后端）、IPC 通信

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

let mainWindow = null;
let backendProc = null;

const BACKEND_PORT = 8765;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/api/health`;

/** 探测后端是否已在运行（避免重复拉起） */
function isBackendUp(timeoutMs = 800) {
  return new Promise((resolve) => {
    const req = http.get(BACKEND_HEALTH_URL, (res) => {
      res.resume();
      resolve(res.statusCode >= 200 && res.statusCode < 500);
    });
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

/** 打包后内嵌后端可执行文件路径（PyInstaller 单目录产物） */
function bundledBackendPath() {
  if (!app.isPackaged) return null;
  const exe = process.platform === 'win32' ? 'localflow-backend.exe' : 'localflow-backend';
  return path.join(process.resourcesPath, 'backend', exe);
}

/** 确保后端运行：未监听 8765 时 spawn 内嵌后端 */
async function ensureBackend() {
  if (await isBackendUp()) return;
  const exe = bundledBackendPath();
  if (!exe) return; // 开发模式：后端由开发者手动启动（npm run dev）
  try {
    backendProc = spawn(exe, [], {
      cwd: path.dirname(exe),
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    backendProc.stdout.on('data', (d) => process.stdout.write(`[backend] ${d}`));
    backendProc.stderr.on('data', (d) => process.stderr.write(`[backend] ${d}`));
    backendProc.on('exit', (code) => {
      console.log(`[backend] 退出 code=${code}`);
      backendProc = null;
    });
    // 等待后端就绪（最多 15s），期间继续创建窗口，前端轮询 /api/health 自会恢复
    const deadline = Date.now() + 15000;
    while (!(await isBackendUp(500)) && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 500));
    }
  } catch (e) {
    console.error('[backend] 启动失败：', e);
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 600,
    title: 'LocalFlow AI',
    backgroundColor: '#f4f6fb',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // 加载渲染器页面
  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));

  // 开发模式打开 DevTools
  if (process.argv.includes('--dev')) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// === 生命周期 ===

app.whenReady().then(() => {
  createWindow();
  ensureBackend(); // 不阻塞窗口创建

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

// 退出时清理后端子进程
app.on('will-quit', () => {
  if (backendProc && !backendProc.killed) {
    try { backendProc.kill(); } catch (e) { /* ignore */ }
  }
});

// === IPC ===

ipcMain.handle('app:get-info', () => {
  return {
    version: app.getVersion(),
    platform: process.platform,
  };
});

ipcMain.handle('app:api-base', () => {
  // 后端 API 地址（默认本地 8765）
  return process.env.LOCALFLOW_API || `http://127.0.0.1:${BACKEND_PORT}`;
});

ipcMain.handle('app:pick-directory', async () => {
  // 系统目录选择器，返回选中目录绝对路径（取消则返回空）
  const win = BrowserWindow.getFocusedWindow() || mainWindow;
  const result = win
    ? await dialog.showOpenDialog(win, {
        title: '选择部署文件夹',
        properties: ['openDirectory', 'createDirectory'],
      })
    : await dialog.showOpenDialog({
        title: '选择部署文件夹',
        properties: ['openDirectory', 'createDirectory'],
      });
  if (result.canceled || !result.filePaths.length) return '';
  return result.filePaths[0];
});
