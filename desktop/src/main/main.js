// Electron 主进程
// 负责：窗口管理、后端进程启动（打包后自动拉起内嵌 Python 后端）、IPC 通信

const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');
const http = require('http');

let mainWindow = null;
let backendProc = null;
let dshProc = null;

const BACKEND_PORT = 8765;
const BACKEND_HEALTH_URL = `http://127.0.0.1:${BACKEND_PORT}/api/health`;

// DeepSeek Harness（dsh）：对话与 Agent 能力由它提供，默认 8080。
// 安装包不内嵌 dsh，但主进程会在其未运行时尝试自动拉起（开发模式用仓库内
// dsh-poc 依赖；打包后用 PATH 中的 dsh）。拉起失败不报错，引导卡兜底提示手动启动。
const DSH_PORT = 8080;
const DSH_BASE = process.env.LOCALFLOW_DSH_HOST
  ? `http://${process.env.LOCALFLOW_DSH_HOST}`
  : `http://127.0.0.1:8080`;
const DSH_HEALTH_URL = `${DSH_BASE}/`;

/**
 * 定位 dsh 可执行文件：
 *  1) 开发模式：优先用仓库内 dsh-poc 的本地依赖（无需全局安装）
 *  2) 打包模式：交给 PATH 中的 `dsh` 解析（用户自行全局安装）
 *  3) 都不存在时返回 'dsh'，由 spawn 解析 PATH；若仍失败则引导卡兜底
 */
function findDshBinary() {
  if (!app.isPackaged) {
    const devBin = path.resolve(__dirname, '..', '..', '..', 'dsh-poc', 'node_modules', '.bin', 'dsh');
    if (fs.existsSync(devBin)) return devBin;
  }
  return 'dsh';
}

/** 若 dsh 未在运行则自动拉起（--profile web）。失败仅记录，不阻塞应用。 */
function tryLaunchDsh() {
  if (dshProc) return;
  // 用户显式指定了远端 dsh 时，不自动拉起本地实例
  if (process.env.LOCALFLOW_DSH_HOST) return;
  isDshUp(800).then((up) => {
    if (up) return; // 已在运行，无需拉起
    const bin = findDshBinary();
    const args = ['--profile', 'web'];
    try {
      dshProc = spawn(bin, args, {
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        detached: false,
      });
      dshProc.stdout.on('data', (d) => process.stdout.write(`[dsh] ${d}`));
      dshProc.stderr.on('data', (d) => process.stderr.write(`[dsh] ${d}`));
      dshProc.on('error', (e) =>
        console.warn('[dsh] 自动拉起失败（可忽略，引导卡会提示手动启动）：', e.message));
      dshProc.on('exit', (code) => {
        console.log(`[dsh] 退出 code=${code}`);
        dshProc = null;
      });
      console.log('[dsh] 已尝试自动拉起 dsh（--profile web）');
    } catch (e) {
      console.warn('[dsh] 自动拉起异常：', e.message);
    }
  });
}

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
  // 注：主进程已尝试自动拉起 dsh（tryLaunchDsh）；此处仅周期探测、不重复拉起
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

/** 探测 Ollama 可执行文件是否在 PATH 中（覆盖「已安装但未运行」的情况） */
function isOllamaInstalled() {
  try {
    const cmd = process.platform === 'win32' ? 'where' : 'command';
    const args = process.platform === 'win32' ? ['ollama'] : ['-v', 'ollama'];
    return spawnSync(cmd, args, { stdio: 'ignore' }).status === 0;
  } catch (e) {
    return false;
  }
}

/** 若 Ollama 已安装但未运行，后台拉起 `ollama serve`（不阻塞，失败仅记录） */
function tryStartOllama() {
  try {
    const p = spawn('ollama', ['serve'], { stdio: 'ignore', detached: true });
    p.unref();
    console.log('[ollama] 已后台拉起 ollama serve');
  } catch (e) {
    console.warn('[ollama] 自动拉起失败：', e.message);
  }
}

/** 首启环境自检：Ollama 缺失/未运行时尽量自动就绪；失败再引导安装 */
async function ensureOllama() {
  if (await isOllamaUp()) return;

  // 已安装但未运行：直接后台拉起，省去手动操作（解决「装了还报错」）
  if (isOllamaInstalled()) {
    tryStartOllama();
    await new Promise((r) => setTimeout(r, 4000));
    if (await isOllamaUp()) return;
  }

  const platform = process.platform;
  const buttons =
    platform === 'darwin'
      ? ['用 Homebrew 安装', '打开官网下载', '我已完成，重试']
      : ['打开官网下载', '我已完成，重试'];

  const { response } = await dialog.showMessageBox({
    type: 'info',
    title: '需要 Ollama 本地推理引擎',
    message: 'LocalFlow AI 依赖本机 Ollama 提供本地模型推理，当前未检测到 Ollama 服务。',
    detail: '若你已安装 Ollama，本应用会尝试自动启动它；否则按下方方式安装后本应用即可直接使用。',
    buttons,
    defaultId: 0,
    cancelId: buttons.length - 1,
  });

  const choice = buttons[response];
  if (choice === '用 Homebrew 安装') {
    spawn('brew', ['install', 'ollama'], { stdio: 'ignore' });
    await new Promise((r) => setTimeout(r, 6000)); // 等待 brew 安装完成
    if (isOllamaInstalled()) tryStartOllama();       // 装完自动拉起
    await new Promise((r) => setTimeout(r, 4000));
    if (await isOllamaUp()) return;
    return ensureOllama(); // 仍未就绪则再次引导
  } else if (choice === '打开官网下载') {
    shell.openExternal('https://ollama.com/download');
  }
  // 「我已完成，重试」或关闭：已安装则尝试拉起，再探一次；仍未就绪也不强制退出
  if (isOllamaInstalled()) {
    tryStartOllama();
    await new Promise((r) => setTimeout(r, 4000));
  }
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
  tryLaunchDsh(); // 未运行时自动拉起对话引擎（失败则引导卡兜底）
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

// 退出时清理子进程
app.on('will-quit', () => {
  if (backendProc && !backendProc.killed) {
    try { backendProc.kill(); } catch (e) { /* ignore */ }
  }
  if (dshProc && !dshProc.killed) {
    try { dshProc.kill(); } catch (e) { /* ignore */ }
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
