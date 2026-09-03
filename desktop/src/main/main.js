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
// 安装包**已内嵌 dsh 与便携 Node**（scripts/bundle-dsh.mjs 产物 → resources/dsh、resources/node），
// 实现「下载即用、离线零安装」：打包态直接用内置 Node 拉起内置 dsh，DSH_HOME 指向 userData 下的
// 可写副本；开发态用仓库内 dsh-poc 依赖；两者都没有才退回 PATH 中的 dsh，失败由引导卡兜底提示。
const DSH_PORT = 8080;
const DSH_BASE = process.env.LOCALFLOW_DSH_HOST
  ? `http://${process.env.LOCALFLOW_DSH_HOST}`
  : `http://127.0.0.1:8080`;
const DSH_HEALTH_URL = `${DSH_BASE}/`;

/**
 * 打包后内置 dsh 入口脚本（scripts/bundle-dsh.mjs 产物 → resources/dsh）。
 *
 * ⚠️ 必须是 .pnpm/<hash>/node_modules/ 下的那份，绝不能用顶层 node_modules/@deepseek-ai/dsh：
 *    pnpm 布局中每个包的依赖是 .pnpm/<hash>/node_modules/ 里的**同级项**，
 *    dsh-app-boot 等传递依赖并不存在于顶层 node_modules，从顶层启动会 MODULE_NOT_FOUND。
 *    hash 随依赖图变化，故运行时按前缀动态查找，避免写死。
 */
/**
 * 内置 dsh 的「安装位置」：userData/dsh-install。
 * 打包后主进程会把 resources/{dsh/node_modules, node} 解包到这里（带版本标记），
 * 实现「首次启动检测 → 已装则比对版本、不符则更新 → 未装则安装」的受控生命周期，
 * 且安装副本可写、不依赖只读的 app 包。DSH_HOME 仍单独播种在 userData/dsh-home。
 */
function dshInstallDir() {
  return path.join(app.getPath('userData'), 'dsh-install');
}

/** 在 userData/dsh-install 中按 .pnpm hash 动态查找 dsh 入口（物化后必须从 .pnpm 启动） */
function dshInstallEntry() {
  const pnpmDir = path.join(dshInstallDir(), 'node_modules', '.pnpm');
  if (!fs.existsSync(pnpmDir)) return null;
  try {
    const hit = fs.readdirSync(pnpmDir).find((n) => n.startsWith('@deepseek-ai+dsh@'));
    if (!hit) return null;
    const p = path.join(pnpmDir, hit, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');
    return fs.existsSync(p) ? p : null;
  } catch (e) {
    return null;
  }
}

/** 在 userData/dsh-install 中查找内置便携 Node */
function dshInstallNode() {
  const exe = process.platform === 'win32' ? 'node.exe' : 'node';
  const p = path.join(dshInstallDir(), 'node', exe);
  return fs.existsSync(p) ? p : null;
}

function bundledDshEntry() {
  if (!app.isPackaged) return null;
  // 优先已安装副本，回退 resources 内嵌包（保证开发/回退可用）
  return dshInstallEntry() || bundledDshEntryFromResources();
}

/** resources/dsh 内嵌包中的 dsh 入口（回退路径） */
function bundledDshEntryFromResources() {
  const pnpmDir = path.join(process.resourcesPath, 'dsh', 'node_modules', '.pnpm');
  if (!fs.existsSync(pnpmDir)) return null;
  try {
    const hit = fs.readdirSync(pnpmDir).find((n) => n.startsWith('@deepseek-ai+dsh@'));
    if (!hit) return null;
    const p = path.join(pnpmDir, hit, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');
    return fs.existsSync(p) ? p : null;
  } catch (e) {
    return null;
  }
}

/** 打包后内置便携 Node（优先 userData/dsh-install/node，回退 resources/node/node，Windows 为 node.exe） */
function bundledNodePath() {
  if (!app.isPackaged) return null;
  const exe = process.platform === 'win32' ? 'node.exe' : 'node';
  const inst = path.join(dshInstallDir(), 'node', exe);
  if (fs.existsSync(inst)) return inst;
  const p = path.join(process.resourcesPath, 'node', exe);
  return fs.existsSync(p) ? p : null;
}

// ── 内置 dsh 受控安装 / 更新生命周期 ──────────────────────────────────────
// 打包后把 resources/{dsh/node_modules, node} 解包到 userData/dsh-install，
// 首次启动检测已装版本、不符则更新、未装则安装，全程经 dsh-install IPC 推送进度。

/** 读取 resources 内嵌 dsh 的版本（用于比对是否需要更新） */
function readBundledDshVersion() {
  const pnpmDir = path.join(process.resourcesPath, 'dsh', 'node_modules', '.pnpm');
  if (!fs.existsSync(pnpmDir)) return null;
  try {
    const hit = fs.readdirSync(pnpmDir).find((n) => n.startsWith('@deepseek-ai+dsh@'));
    if (!hit) return null;
    const pkg = path.join(pnpmDir, hit, 'node_modules', '@deepseek-ai', 'dsh', 'package.json');
    if (!fs.existsSync(pkg)) return null;
    try { return JSON.parse(fs.readFileSync(pkg, 'utf8')).version || null; } catch (e) { return null; }
  } catch (e) {
    return null;
  }
}

/** 读取已安装副本的版本标记（userData/dsh-install/.version） */
function readInstalledDshVersion() {
  try {
    const f = path.join(dshInstallDir(), '.version');
    return fs.existsSync(f) ? fs.readFileSync(f, 'utf8').trim() : null;
  } catch (e) {
    return null;
  }
}

/** 递归统计目录（或单文件）总字节数，用于进度百分比 */
function countBytes(root) {
  let total = 0;
  let st;
  try { st = fs.statSync(root); } catch (e) { return 0; }
  if (st.isFile()) return st.size;
  const walk = (d) => {
    let s;
    try { s = fs.statSync(d); } catch (e) { return; }
    if (s.isDirectory()) {
      let es;
      try { es = fs.readdirSync(d, { withFileTypes: true }); } catch (e) { return; }
      for (const e of es) walk(path.join(d, e.name));
    } else if (s.isFile()) {
      total += s.size;
    }
  };
  walk(root);
  return total;
}

/**
 * 递归复制（目录/单文件均可），按已复制字节数回调进度，并周期性让出主线程以刷新进度条。
 * counter: { total, copied } 在多次复制之间共享，emit(copied, total) 推送进度。
 */
async function copyTreeWithProgress(src, dest, counter, emit) {
  let st;
  try { st = fs.statSync(src); } catch (e) { return; }
  if (st.isFile()) {
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
    counter.copied += st.size;
    emit(counter.copied, counter.total);
    await new Promise((r) => setImmediate(r));
    return;
  }
  fs.mkdirSync(dest, { recursive: true });
  let lastEmit = counter.copied;
  const copyDir = async (s, d) => {
    fs.mkdirSync(d, { recursive: true });
    const es = fs.readdirSync(s, { withFileTypes: true });
    for (const e of es) {
      const sp = path.join(s, e.name);
      const dp = path.join(d, e.name);
      if (e.isSymbolicLink()) {
        let real;
        try { real = fs.realpathSync(sp); } catch (err) { continue; }
        let rs;
        try { rs = fs.statSync(real); } catch (err) { continue; }
        if (rs.isDirectory()) await copyDir(real, dp);
        else { fs.copyFileSync(real, dp); counter.copied += rs.size; maybeEmit(); }
      } else if (e.isDirectory()) {
        await copyDir(sp, dp);
      } else {
        let sz = 0;
        try { sz = fs.statSync(sp).size; } catch (err) { sz = 0; }
        fs.copyFileSync(sp, dp);
        counter.copied += sz;
        maybeEmit();
      }
    }
  };
  const maybeEmit = () => {
    if (counter.copied - lastEmit > 8 * 1024 * 1024) {
      lastEmit = counter.copied;
      emit(counter.copied, counter.total);
    }
  };
  await copyDir(src, dest);
}

/** dsh 安装进度的最新状态（供渲染进程加载时取快照，规避事件竞态） */
let lastDshInstallState = { status: 'idle' };
let ensuringDsh = false;

function sendDshInstall(win, state) {
  lastDshInstallState = { ...lastDshInstallState, ...state };
  if (win && !win.isDestroyed()) {
    try { win.webContents.send('dsh-install', lastDshInstallState); } catch (e) { /* ignore */ }
  }
}

const MB = (b) => `${(b / 1024 / 1024).toFixed(0)} MB`;

/**
 * 受控安装/更新：检测内置 dsh 版本，与已装版本比对。
 * - 已装且版本一致 → 直接 ready（不复制）
 * - 未装或版本不符 → 把 resources/{dsh/node_modules, node} 解包到 userData/dsh-install（带进度），写版本标记并播种 DSH_HOME
 * 失败推送 status:'error'，由前端提供重试。
 */
async function ensureDsh(win) {
  if (!app.isPackaged) {
    lastDshInstallState = { status: 'idle' };
    return; // 开发态直接用 dsh-poc，无需解包
  }
  if (ensuringDsh) return;
  ensuringDsh = true;
  try {
    const bundledVer = readBundledDshVersion();
    const installedVer = readInstalledDshVersion();
    sendDshInstall(win, {
      status: 'progress', phase: 'checking', percent: 0,
      message: installedVer ? `已安装版本 ${installedVer}` : '未检测到已安装版本',
    });

    // 已安装且版本一致 → 无需解包
    if (installedVer && bundledVer && installedVer === bundledVer) {
      ensureDshHome();
      sendDshInstall(win, {
        status: 'done', phase: 'ready', percent: 100, uptodate: true,
        message: `已是最新版本（${installedVer}）`,
      });
      return;
    }

    const reason = installedVer
      ? `从 ${installedVer} 更新到 ${bundledVer}`
      : `首次安装 ${bundledVer}`;
    const nmSrc = path.join(process.resourcesPath, 'dsh', 'node_modules');
    const nodeSrc = path.join(process.resourcesPath, 'node');
    const counter = { total: countBytes(nmSrc) + countBytes(nodeSrc), copied: 0 };
    const destRoot = dshInstallDir();

    sendDshInstall(win, { status: 'progress', phase: 'copying', percent: 0, message: `准备${reason}…` });
    try {
      // 版本不符时先清空旧安装目录，保证干净覆盖
      fs.rmSync(destRoot, { recursive: true, force: true });
      const emit = (c, t) => {
        const pct = t ? (c / t) * 100 : 0;
        sendDshInstall(win, {
          status: 'progress', phase: 'copying', percent: pct,
          message: `已解包 ${MB(c)} / ${MB(t)}（${reason}）`,
        });
      };
      await copyTreeWithProgress(nmSrc, path.join(destRoot, 'node_modules'), counter, emit);
      await copyTreeWithProgress(nodeSrc, path.join(destRoot, 'node'), counter, emit);
      emit(counter.copied, counter.total);

      // 确保便携 Node 保留可执行位（跨平台复制偶发丢失执行位）
      try {
        const nodeExe = path.join(destRoot, 'node', process.platform === 'win32' ? 'node.exe' : 'node');
        if (fs.existsSync(nodeExe)) fs.chmodSync(nodeExe, 0o755);
      } catch (e) { /* ignore */ }

      fs.writeFileSync(path.join(destRoot, '.version'), bundledVer || '', 'utf8');
      sendDshInstall(win, { status: 'progress', phase: 'seeding', percent: 100, message: '正在初始化配置…' });

      // 播种可写 DSH_HOME（含 settings.yaml，配置 local-flow provider）
      ensureDshHome();

      sendDshInstall(win, {
        status: 'done', phase: 'ready', percent: 100, uptodate: false,
        message: `安装完成（${bundledVer}）`,
      });
    } catch (e) {
      sendDshInstall(win, {
        status: 'error',
        phase: 'copying',
        percent: counter.total ? (counter.copied / counter.total) * 100 : 0,
        message: e && e.message ? e.message : String(e),
      });
    }
  } finally {
    ensuringDsh = false;
  }
}

/** 递归复制目录，跳过会话/存储/截图等隐私与运行态数据 */
function copyDirSync(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (e.name === 'sessions' || e.name === 'storages' || e.name === 'ui-screenshots') continue;
    const s = path.join(src, e.name);
    const d = path.join(dest, e.name);
    if (e.isDirectory()) copyDirSync(s, d);
    else if (e.isFile()) fs.copyFileSync(s, d);
  }
}

/**
 * dsh 的 home：$DSH_HOME/profiles 决定 --profile web 加载哪个 profile。
 * 必须可写（dsh 会写 sessions/storages/settings），故放在 userData 下而非只读的 app 包内；
 * 首次运行从内置模板（或开发态 dsh-poc/dsh-home）播种一份，保证开箱即用。
 * 注：模板中的 settings.yaml 是有意保留的——它配置 LocalFlow 本地 provider（local-flow）
 * 让 dsh 直接对接本机 Ollama 模型，且不含任何密钥。
 */
function ensureDshHome() {
  const home = path.join(app.getPath('userData'), 'dsh-home');
  try {
    if (!fs.existsSync(path.join(home, 'profiles', 'web'))) {
      const tpl = app.isPackaged
        ? path.join(process.resourcesPath, 'dsh', 'dsh-home')
        : path.resolve(__dirname, '..', '..', '..', 'dsh-poc', 'dsh-home');
      if (fs.existsSync(tpl)) {
        copyDirSync(tpl, home);
        console.log('[dsh] 已从模板播种 DSH_HOME：', home);
      } else {
        fs.mkdirSync(path.join(home, 'profiles', 'web'), { recursive: true });
      }
    }
  } catch (e) {
    console.warn('[dsh] 准备 DSH_HOME 失败：', e.message);
  }
  return home;
}

/**
 * 解析 dsh 启动方式，返回 { cmd, args, env }。
 * 优先级：① 打包态内置 Node + 内置 dsh（零安装、离线可用）
 *         ② 开发态仓库内 dsh-poc 本地依赖
 *         ③ 兜底 PATH 中的 `dsh`（失败则由引导卡提示手动启动）
 */
function resolveDshLaunch() {
  const env = { ...process.env, DSH_HOME: ensureDshHome() };
  const nodeBin = bundledNodePath();
  const entry = bundledDshEntry();
  if (nodeBin && entry) return { cmd: nodeBin, args: [entry, '--profile', 'web'], env };
  if (!app.isPackaged) {
    const devBin = path.resolve(__dirname, '..', '..', '..', 'dsh-poc', 'node_modules', '.bin', 'dsh');
    if (fs.existsSync(devBin)) return { cmd: devBin, args: ['--profile', 'web'], env };
  }
  return { cmd: 'dsh', args: ['--profile', 'web'], env };
}

/** dsh 运行日志路径（写入 userData，便于排查启动失败） */
function dshLogPath() {
  return path.join(app.getPath('userData'), 'dsh.log');
}

/**
 * 最近一次 dsh 拉起的诊断信息。dsh 若是长驻 web 服务却在启动后退出，
 * 几乎必然是崩溃（native 模块 ABI 不匹配、配置缺失、端口被占等）。
 * 我们将 stderr 尾部 + 退出码 + 解析出的完整命令回传前端，让「对话引擎未连接」
 * 从一团迷雾变成可复制粘贴的可诊断错误，而不是静默失败。
 */
let dshLaunchInfo = null;        // 当前/最近一次拉起的诊断
let lastDshLaunchError = null;   // 供渲染进程加载时取快照
let dshAppQuitting = false;      // 退出时不再把正常 kill 当作崩溃

function dshLaunchShellCommand(info) {
  if (!info) return '';
  const home = info.env && info.env.DSH_HOME ? info.env.DSH_HOME : '';
  const cmd = [info.cmd, ...(info.args || [])].map((a) => `"${a}"`).join(' ');
  return home ? `DSH_HOME="${home}" ${cmd}` : cmd;
}

function appendDshLog(line) {
  try {
    fs.appendFileSync(dshLogPath(), line.endsWith('\n') ? line : line + '\n');
  } catch (e) { /* 日志写入失败不阻塞主流程 */ }
  if (dshLaunchInfo && dshLaunchInfo.stderrTail) {
    dshLaunchInfo.stderrTail.push(line);
    if (dshLaunchInfo.stderrTail.length > 60) dshLaunchInfo.stderrTail.shift();
  }
}

function sendDshLaunchError() {
  if (!dshLaunchInfo) return;
  if (dshAppQuitting) return; // 应用退出导致的 kill 不算崩溃
  const info = dshLaunchInfo;
  const tail = info.stderrTail ? info.stderrTail.join('').trim() : '';
  const message = (tail || info.lastError || '未捕获到输出（进程可能秒退）').slice(-2000);
  lastDshLaunchError = {
    status: 'launch-error',
    message,
    exitCode: info.exited ? info.exitCode : null,
    exitSignal: info.exited ? info.exitSignal : null,
    command: dshLaunchShellCommand(info),
    cmd: info.cmd,
    args: info.args,
    env: { DSH_HOME: (info.env && info.env.DSH_HOME) || null },
    logPath: dshLogPath(),
    launchedAt: info.launchedAt,
  };
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send('dsh-launch-error', lastDshLaunchError); } catch (e) { /* ignore */ }
  }
}

/** 若 dsh 未在运行则自动拉起（--profile web）。失败仅记录，不阻塞应用。 */
function tryLaunchDsh() {
  if (dshProc) return;
  // 用户显式指定了远端 dsh 时，不自动拉起本地实例
  if (process.env.LOCALFLOW_DSH_HOST) return;
  isDshUp(800).then((up) => {
    if (up) return; // 已在运行，无需拉起
    const launch = resolveDshLaunch();
    if (!launch || !launch.cmd) {
      console.warn('[dsh] 无法解析本地 dsh 启动方式，交由引导卡提示手动启动');
      return;
    }
    try {
      // 初始化本次拉起的诊断信息
      dshLaunchInfo = {
        cmd: launch.cmd,
        args: launch.args,
        env: launch.env,
        launchedAt: Date.now(),
        exited: false,
        exitCode: null,
        exitSignal: null,
        lastError: null,
        stderrTail: [],
      };
      appendDshLog(`\n===== dsh 拉起 @ ${new Date().toISOString()} =====`);
      appendDshLog(`$ ${dshLaunchShellCommand(dshLaunchInfo)}`);
      dshProc = spawn(launch.cmd, launch.args, {
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true,
        detached: false,
        env: launch.env,
      });
      dshProc.stdout.on('data', (d) => {
        const s = d.toString();
        process.stdout.write(`[dsh] ${s}`);
        appendDshLog('[out] ' + s.replace(/\n$/, ''));
      });
      dshProc.stderr.on('data', (d) => {
        const s = d.toString();
        process.stderr.write(`[dsh] ${s}`);
        appendDshLog('[err] ' + s.replace(/\n$/, ''));
        if (dshLaunchInfo) dshLaunchInfo.lastError = s.trim().slice(-500);
      });
      dshProc.on('error', (e) => {
        console.warn('[dsh] 自动拉起失败（可忽略，引导卡会提示手动启动）：', e.message);
        if (dshLaunchInfo) dshLaunchInfo.lastError = e.message;
        appendDshLog('[error] ' + e.message);
        sendDshLaunchError();
      });
      dshProc.on('exit', (code, signal) => {
        console.log(`[dsh] 退出 code=${code} signal=${signal}`);
        if (dshLaunchInfo) {
          dshLaunchInfo.exited = true;
          dshLaunchInfo.exitCode = code;
          dshLaunchInfo.exitSignal = signal;
        }
        appendDshLog(`[exit] code=${code} signal=${signal}`);
        dshProc = null;
        sendDshLaunchError(); // 长驻服务退出 = 崩溃，回传诊断
      });
      console.log('[dsh] 已尝试自动拉起：', launch.cmd, launch.args.join(' '),
        '| DSH_HOME=', launch.env.DSH_HOME);
    } catch (e) {
      console.warn('[dsh] 自动拉起异常：', e.message);
      if (dshLaunchInfo) dshLaunchInfo.lastError = e.message;
      appendDshLog('[exception] ' + e.message);
      sendDshLaunchError();
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
async function monitorDsh() {
  // 始终引用全局 mainWindow：窗口可能被关掉后重建（关红叉不退出 app 再重开），
  // 若捕获旧引用会在旧窗口销毁后停掉整个探测循环，导致新窗口永不收到「已连接」信号。
  const win = mainWindow;
  if (win && !win.isDestroyed()) {
    try {
      const up = await isDshUp();
      win.webContents.send('dsh-status', up);
    } catch (e) { /* ignore */ }
  }
  // 无论窗口是否存在都继续轮询；窗口重建后下一轮自动恢复推送，避免探测循环永久停止
  setTimeout(() => monitorDsh(), 5000);
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

app.whenReady().then(async () => {
  createWindow();
  ensureBackend(); // 不阻塞窗口创建
  // 先确保内置 dsh 已安装/更新（带进度条），再拉起对话引擎
  await ensureDsh(mainWindow);
  tryLaunchDsh(); // 未运行时自动拉起对话引擎（失败则引导卡兜底）
  ensureOllama(); // 不阻塞：缺失则弹引导框，按平台分流安装
  monitorDsh(); // dsh 状态周期探测并推送给前端（内部引用全局 mainWindow，窗口重建后仍有效）

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
  dshAppQuitting = true; // 不再把退出期的正常 kill 当作崩溃上报
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

ipcMain.handle('app:dsh-install-state', () => {
  // 渲染进程加载时取一次快照，规避早期进度事件竞态
  return lastDshInstallState;
});

ipcMain.handle('app:dsh-install-retry', async () => {
  // 安装失败后「重试」：清空失败的安装目录并重新解包
  if (!app.isPackaged) return { status: 'idle' };
  try { fs.rmSync(dshInstallDir(), { recursive: true, force: true }); } catch (e) { /* ignore */ }
  await ensureDsh(mainWindow);
  return lastDshInstallState;
});

ipcMain.handle('app:dsh-launch-error-state', () => {
  // 渲染进程加载时取一次快照，避免错过早期崩溃诊断
  return lastDshLaunchError;
});

ipcMain.handle('app:dsh-relaunch', async () => {
  // 「重新连接」时若本地 dsh 已退出，再尝试拉起一次（用户主动触发，避免死循环：
  // 仅在 dsh 当前未运行时拉起；若仍崩溃会再次回传诊断而非无限重启）
  if (!app.isPackaged && process.env.LOCALFLOW_DSH_HOST) return { status: 'remote' };
  dshLaunchInfo = null;
  lastDshLaunchError = null;
  // 先确认确实没在监听，避免重复拉起
  const up = await isDshUp(800);
  if (up) return { status: 'up' };
  tryLaunchDsh();
  return { status: 'relaunched' };
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
