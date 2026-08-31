#!/usr/bin/env node
/**
 * bundle-dsh.mjs — 把 DeepSeek Harness（dsh）打包成「可重定位 / 离线零安装」的资源。
 *
 * 背景：dsh 是 LocalFlow 对话与 Agent 能力的提供方。@deepseek-ai/dsh 本身是 npm 公开包
 * （MIT 协议，允许再分发），但 dsh-poc 用 pnpm 管理，其 .bin/dsh 启动脚本会把仓库绝对路径
 * （含本机用户名）写死进 NODE_PATH —— 直接复制进安装包既会在别的机器上失效，又会泄漏开发者
 * 路径。因此本脚本跟随软链物化一份自包含副本，并剔除会话记录等隐私数据。
 *
 * 本脚本产出两份自包含资源（放在 desktop/build/，纳入 electron-builder extraResources）：
 *   1) dsh-bundle/   —— 跟随软链（follow symlinks）复制 dsh-poc/node_modules 与 dsh-poc/dsh-home，
 *                      物化为不依赖任何绝对路径的目录；并 patch 掉 .bin/dsh 里的绝对 NODE_PATH。
 *   2) node-bundle/ —— 与平台/架构匹配的便携式 Node 22 运行时（dsh 要求 >=22.19.0）。
 *
 * 随后 electron-builder 把它们放进安装包的 resources/dsh 与 resources/node。
 * 运行时由主进程（src/main/main.js）用内置 Node 拉起内置 dsh，并把 DSH_HOME 指向用户
 * 数据目录下的一份可写副本，实现下载即用的零安装。
 *
 * 用法：node desktop/scripts/bundle-dsh.mjs   （在 CI 构建与本地 `npm run build:*` 前自动调用）
 */

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { execSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(__dirname, '..');          // desktop/
const repoRoot = path.resolve(desktopDir, '..');          // 仓库根
const dshPocDir = path.join(repoRoot, 'dsh-poc');
const outRoot = path.join(desktopDir, 'build');
const dshBundle = path.join(outRoot, 'dsh-bundle');
const nodeBundle = path.join(outRoot, 'node-bundle');

// 便携式 Node 版本（满足 dsh engines.node >=22.19.0）
const NODE_VERSION = '22.22.2';

const log = (...a) => console.log('[bundle-dsh]', ...a);
const warn = (...a) => console.warn('[bundle-dsh]', ...a);

/** 是否跳过某条相对路径（隐私清理 + 体积控制） */
function shouldSkip(rel) {
  // 绝对路径泄漏源 / 无关产物
  if (rel.includes('ui-screenshots')) return true;
  if (rel.includes('.git')) return true;
  if (/\.log$/i.test(rel)) return true;
  // pnpm 缓存与运行态数据，无需打包
  if (rel.includes('node_modules/.cache')) return true;
  if (rel.includes('node_modules/.pnpm') && rel.includes('storages')) return true;
  if (rel.split(path.sep).includes('storages')) return true;
  // ⚠️ 会话记录（隐私红线）：dsh-home/sessions/ 下的目录名直接内嵌开发者绝对路径
  //    （如 --Users-<用户名>-Documents-...-dsh-poc--），且内容为历史对话，
  //    一旦打进公开安装包即泄漏个人信息与会话内容，必须排除。
  //    注：settings.yaml 需保留——它是 LocalFlow 本地 provider（local-flow）的模型配置，
  //    让 dsh 开箱即对接本机 Ollama 模型，且经核查不含任何密钥。
  if (rel.split(path.sep).includes('sessions')) return true;
  if (rel.endsWith('.jsonl.zstd')) return true;
  return false;
}

/**
 * 跟随软链递归复制：把 symlink 解析为真实目标后复制其内容，
 * 产出完全自包含、不含任何绝对路径依赖的目录树。
 */
function copyDirFollow(src, dest, relBase = '') {
  fs.mkdirSync(dest, { recursive: true });
  const entries = fs.readdirSync(src, { withFileTypes: true });
  for (const e of entries) {
    const rel = relBase ? path.join(relBase, e.name) : e.name;
    if (shouldSkip(rel)) continue;
    const srcPath = path.join(src, e.name);
    const destPath = path.join(dest, e.name);
    let stat;
    try { stat = fs.lstatSync(srcPath); } catch { continue; }
    if (stat.isSymbolicLink()) {
      let real;
      try { real = fs.realpathSync(srcPath); } catch { continue; }
      let realStat;
      try { realStat = fs.statSync(real); } catch { continue; }
      if (realStat.isDirectory()) copyDirFollow(real, destPath, rel);
      else fs.copyFileSync(real, destPath);
    } else if (stat.isDirectory()) {
      copyDirFollow(srcPath, destPath, rel);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

/** 删除 .bin/dsh 中写死本机绝对路径的 NODE_PATH 导出行与注释行（隐私 + 可重定位） */
function patchDshShim() {
  const shim = path.join(dshBundle, 'node_modules', '.bin', 'dsh');
  if (!fs.existsSync(shim)) {
    warn('未找到 .bin/dsh，跳过 shim patch（可能已被 npm 安装覆盖，无碍）');
    return;
  }
  let txt = fs.readFileSync(shim, 'utf8');
  const before = txt.length;
  // 去掉把仓库绝对路径写进 NODE_PATH 的导出行
  txt = txt.replace(/^\s*export\s+NODE_PATH=.*$/m, '');
  // 去掉含本机绝对路径的 cmd-shim-target 注释行
  txt = txt.replace(/^#\s*cmd-shim-target=.*$/m, '');
  if (txt.length !== before) {
    fs.writeFileSync(shim, txt);
    log('已 patch .bin/dsh：移除写死绝对路径的 NODE_PATH / cmd-shim-target');
  } else {
    log('.bin/dsh 未发现需 patch 的绝对路径（可能已是相对形式）');
  }
}

/** 下载文件到目标路径 */
function download(url, dest) {
  log('下载:', url);
  const isWin = process.platform === 'win32';
  // 优先用系统 curl（CI runner 与 Mac 均具备），Windows 用 powershell
  if (isWin) {
    execSync(
      `powershell -NoProfile -Command "Invoke-WebRequest -Uri '${url}' -OutFile '${dest}'"`,
      { stdio: 'inherit' }
    );
  } else {
    execSync(`curl -fSL "${url}" -o "${dest}"`, { stdio: 'inherit' });
  }
}

/** 按平台/架构下载并解包便携式 Node 到 nodeBundle/node（win 为 node.exe） */
function bundleNode() {
  const plat = process.platform; // darwin | win32 | linux
  const arch = process.arch;     // arm64 | x64
  const v = NODE_VERSION;
  let url, binName, extract;
  if (plat === 'darwin' && arch === 'arm64') {
    url = `https://nodejs.org/dist/v${v}/node-v${v}-darwin-arm64.tar.gz`;
    binName = 'node'; extract = 'tar';
  } else if (plat === 'darwin' && arch === 'x64') {
    url = `https://nodejs.org/dist/v${v}/node-v${v}-darwin-x64.tar.gz`;
    binName = 'node'; extract = 'tar';
  } else if (plat === 'linux' && arch === 'x64') {
    url = `https://nodejs.org/dist/v${v}/node-v${v}-linux-x64.tar.gz`;
    binName = 'node'; extract = 'tar';
  } else if (plat === 'win32' && arch === 'x64') {
    url = `https://nodejs.org/dist/v${v}/node-v${v}-win-x64.zip`;
    binName = 'node.exe'; extract = 'zip';
  } else {
    throw new Error(`暂不支持的平台/架构组合：${plat}/${arch}（如需请扩展本脚本）`);
  }

  fs.mkdirSync(nodeBundle, { recursive: true });
  const tmp = path.join(outRoot, `.node-dl-${plat}-${arch}`);
  fs.rmSync(tmp, { recursive: true, force: true });
  fs.mkdirSync(tmp, { recursive: true });
  const archive = path.join(tmp, path.basename(url));
  download(url, archive);

  if (extract === 'tar') {
    execSync(`tar -xzf "${archive}" -C "${tmp}"`, { stdio: 'inherit' });
    const inner = path.join(tmp, `node-v${v}-${plat === 'darwin' ? 'darwin' : 'linux'}-${arch}`);
    const nodeBin = path.join(inner, binName);
    fs.copyFileSync(nodeBin, path.join(nodeBundle, binName));
    fs.chmodSync(path.join(nodeBundle, binName), 0o755);
  } else {
    const dest = path.join(tmp, 'unzip');
    fs.mkdirSync(dest, { recursive: true });
    execSync(
      `powershell -NoProfile -Command "Expand-Archive -Path '${archive}' -DestinationPath '${dest}'"`,
      { stdio: 'inherit' }
    );
    const inner = path.join(dest, `node-v${v}-win-${arch}`);
    fs.copyFileSync(path.join(inner, binName), path.join(nodeBundle, binName));
  }
  fs.rmSync(tmp, { recursive: true, force: true });
  log('便携式 Node 已就位：', path.join(nodeBundle, binName));
}

function main() {
  if (!fs.existsSync(dshPocDir)) {
    throw new Error(`未找到 dsh-poc 目录：${dshPocDir}（请确认仓库结构）`);
  }
  fs.rmSync(dshBundle, { recursive: true, force: true });
  fs.mkdirSync(dshBundle, { recursive: true });

  log('跟随复制 node_modules（物化为自包含目录，可能耗时数十秒）…');
  copyDirFollow(path.join(dshPocDir, 'node_modules'), path.join(dshBundle, 'node_modules'));

  log('跟随复制 dsh-home 模板…');
  copyDirFollow(path.join(dshPocDir, 'dsh-home'), path.join(dshBundle, 'dsh-home'));

  patchDshShim();

  log('打包便携式 Node 运行时…');
  bundleNode();

  log('完成：');
  log('  dsh 资源  ->', dshBundle);
  log('  node 资源 ->', nodeBundle);
  log('随后 electron-builder 会把它们放入安装包 resources/dsh 与 resources/node。');
}

try {
  main();
} catch (e) {
  console.error('[bundle-dsh] 失败：', e.message);
  process.exit(1);
}
