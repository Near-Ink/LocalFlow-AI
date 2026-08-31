// Electron 主进程
// 负责：窗口管理、后端进程启动（打包后自动拉起内嵌 Python 后端）、IPC 通信

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

let mainWindow = null;
let backendProc = null;

const BACKEND_PORT = 8765;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/api/health`;

// DeepSeek Harness（dsh）：对话与 Agent 能力由它提供，默认 8080。
// 安装包不内嵌 dsh（方案 A：引导用户自备），故只探测、不拉起。
const DSH_PORT = 8080;
const DSH_BASE = process.env.LOCALFLOW_DSH_HOST
  ? `http://${process.env.LOCALFLOW_DSH_HOST}`
  : `http://127.0.0.1:8080`;
const DSH_HEALTH_URL = `${DSH_BASE}/`;

/** 探测 dsh 是否在运行（方案 A：仅探测，不拉起） */
function isDshUp(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(DSH_HEALTH_URL, (res) => {
      res.resume();
      resolve(true);
    });
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

/** 周期推送 dsh 可达状态给渲染进程，驱动「引导卡 / 对话页」切换 */
async function monitorDsh(win) {
  if (!win || win.isDestroyed()) return;
  const up = await isDshUp();
  try { win.webContents.send('dsh-status', up); } catch (e) { /* ignore */ }
  setTimeout(() => monitorDsh(win), 5000);
}

// ── Ollama（本地推理引擎）首启自检 ──────────────────────────────────────────
// 应用唯一外部依赖是 Ollama；首次启动检测，缺失则引导安装，做到「打开即用」。
const OLLAMA_PORT = 11434;
const OLLAMA_HEALTH_URL = `http://127.0.0.1:${OLLAMA_PORT}/api/tags`;

function isOllamaUp(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(OLLAMA_HEALTH_URL, (res) => {
      res.resume();
      resolve(true);
    });
    req.setTimeout(timeoutMs, () => { req.destroy(); resolve(false); });
    req.on('error', () => resolve(false));
  });
}

/** 首启环境自检：Ollama 缺失时弹原生引导框，按平台分流一键安装 */
async function ensureOllama() {
  if (await isOllamaUp()) return;

  const platform = process.platform;
  const buttons =
    platform === 'darwin'
      ? ['用 Homebrew 安装', '打开官网下载', '我已完成，重试']
      : ['打开官网下载', '我已完成，重试'];

  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: '需要 Ollama 本地推理引擎',
    message: 'LocalFlow AI 依赖本机 Ollama 提供本地模型推理，当前未检测到 Ollama。',
    detail: '按下方方式安装后，本应用即可直接使用，无需其他配置。',
    buttons,
    defaultId: 0,
    cancelId: buttons.length - 1,
  });

  const choice = buttons[response];
  if (choice === '用 Homebrew 安装') {
    try {
      spawn('brew', ['install', 'ollama'], { stdio: 'ignore' });
      await new Promise((r) => setTimeout(r, 4000));
      return ensureOllama(); // 安装后重试
    } catch (e) {
      shell.openExternal('https://ollama.com/download');
    }
  } else if (choice === '打开官网下载') {
    shell.openExternal('https://ollama.com/download');
  }
  // 「我已完成，重试」或关闭：再探一次；仍未就绪也不强制退出（对话页仍可用）
  if (await isOllamaUp()) return;
}

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
  ensureOllama(); // 不阻塞：缺失则弹引导框，按平台分流安装
  monitorDsh(mainWindow); // dsh 状态周期探测并推送给前端

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

ipcMain.handle('app:dsh-base', () => {
  // DeepSeek Harness 地址（默认本地 8080）
  return DSH_BASE;
});

ipcMain.handle('app:ollama-status', async () => await isOllamaUp());

ipcMain.handle('app:install-ollama', async () => {
  // 供渲染进程「一键安装」按钮调用：macOS 优先 brew，其余打开官网下载
  if (process.platform === 'darwin') {
    try {
      spawn('brew', ['install', 'ollama'], { stdio: 'ignore' });
      return { method: 'brew' };
    } catch (e) { /* fallthrough 到官网 */ }
  }
  shell.openExternal('https://ollama.com/download');
  return { method: 'web' };
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
