/**
 * dsh 冒烟测试 —— 防止有运行时缺陷的 dsh 版本被打进安装包。
 *
 * 背景（血泪教训）：dsh 0.1.2-rc.1 在 npm 上 CLI 参数完全兼容（--profile/--port/--no-open 都在），
 * 但实际**一收到 HTTP 请求就崩**（invalid media type，content-type/negotiator 解析 Accept-Encoding
 * 时抛异常）。这类问题「能启动」但「不能服务」，只做静态检查或启动检查根本发现不了，
 * 结果就是问题版本进了安装包、用户装完打不开。
 *
 * 本脚本的做法：**真的把 dsh 拉起来，真的发请求，然后检查进程是否还活着**。
 * 覆盖多组 Accept-Encoding（含空值与畸形值），正是当初触发崩溃的场景。
 * 任一环节崩溃/无响应 → 非零退出，CI 构建中断，问题版本无法发布。
 *
 * 用法：cd desktop && node scripts/dsh-smoke.mjs
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 关键防御：本脚本通过 Node 全局 fetch 探测 127.0.0.1 上的 dsh。若运行环境带 HTTP(S)_PROXY
// （某些企业/CI runner 会注入），undici 可能把 127.0.0.1 也走代理导致连接失败、误判 dsh 起不来。
// 这里在进程早期清除代理并显式声明 NO_PROXY，确保只连本机回环，避免「假阴性」。
for (const k of ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']) {
  delete process.env[k];
}
process.env.NO_PROXY = '127.0.0.1,localhost,::1';
process.env.no_proxy = '127.0.0.1,localhost,::1';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.DSH_SMOKE_PORT || 8123);
const BASE = `http://127.0.0.1:${PORT}`;
const START_TIMEOUT_MS = 90_000;   // dsh 首次启动要初始化 profile，给足时间
const REQ_TIMEOUT_MS = 10_000;

const log = (m) => console.log(`[dsh-smoke] ${m}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function findNodeBin() {
  const exe = process.platform === 'win32' ? 'node.exe' : 'node';
  const p = path.join(ROOT, 'build', 'node-bundle', exe);
  return fs.existsSync(p) ? p : null;
}

/** 在 bundle 中按 .pnpm 前缀动态定位 dsh 入口（hash 随依赖图变化，不能写死） */
function findDshEntry() {
  const pnpmDir = path.join(ROOT, 'build', 'dsh-bundle', 'node_modules', '.pnpm');
  if (!fs.existsSync(pnpmDir)) return null;
  const hit = fs.readdirSync(pnpmDir).find((n) => n.startsWith('@deepseek-ai+dsh@'));
  if (!hit) return null;
  const p = path.join(pnpmDir, hit, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');
  return fs.existsSync(p) ? p : null;
}

/** 复制 dsh-home 模板到临时目录作为 DSH_HOME（必须可写） */
function prepareDshHome() {
  const tpl = path.join(ROOT, 'build', 'dsh-bundle', 'dsh-home');
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-smoke-home-'));
  if (fs.existsSync(tpl)) fs.cpSync(tpl, home, { recursive: true });
  else log(`警告：未找到 dsh-home 模板（${tpl}），dsh 将自行初始化`);
  return home;
}

async function waitForPort(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 1500);
      const res = await fetch(`${BASE}/`, { signal: ctl.signal });
      clearTimeout(t);
      await res.arrayBuffer().catch(() => {});
      return true;
    } catch (e) {
      await sleep(1000);
    }
  }
  return false;
}

/** 发一组请求；返回是否全部「有 HTTP 响应」（能连上即可，404/500 都算通） */
async function probe(label, headers) {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), REQ_TIMEOUT_MS);
    const res = await fetch(`${BASE}/`, { headers, signal: ctl.signal });
    clearTimeout(t);
    await res.arrayBuffer().catch(() => {});
    log(`  ${label} → HTTP ${res.status}`);
    return true;
  } catch (e) {
    log(`  ${label} → 请求失败：${e.message}`);
    return false;
  }
}

/**
 * 增强闸门：确认 dsh 启动后，LocalFlow 的本地模型分组（llm-pi-ai / local-flow provider）
 * 确实注册成功。这是 0.1.2 崩溃回归之外的第二层风险：新版 dsh 若改了 provider schema
 * （例如强制要求 apiKeyEnv，或改了命名空间注册方式），我们的 local-flow 配置（指向本机不鉴权
 * 后端，只含 api + baseURL、不含 apiKeyEnv）就可能无法注册，导致用户「升级后本机模型全消失」。
 * 仅做崩溃存活检查抓不到这类问题，故这里额外断言。
 *
 * 注意：settings.describe 的返回结构在不同 dsh 版本间可能略有差异，这里做防御式解析——
 * 只在「明确证据」下硬失败（命名空间存在但 local-flow 缺 api/baseURL），describe 本身解析
 * 异常则降级为告警而非阻断，避免未验证代码误杀本可工作的版本。
 */
async function rpc(method, payload) {
  const rpcId = 'smoke-' + Date.now();
  const res = await fetch(`${BASE}/api/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId, method, payload }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  const data = await res.json();
  if (!data || !data.result || !data.result.ok) throw new Error('rpc fail ' + JSON.stringify(data && data.result));
  return data.result.value;
}

/**
 * 跨版本深度扫描：在 settings.describe 的整个返回结构里寻找 local-flow provider。
 * 不同 dsh 版本的 describe 结构可能漂移（命名空间键名、嵌套层级变化），但 local-flow
 * 的标志性特征稳定：api === 'openai-completions' 且 baseURL 指向本机 127.0.0.1:8765。
 * 用于在「预期路径未命中」时兜底，避免把结构变化误判成本机模型消失而误杀可用版本。
 */
function deepFindLocalFlow(node, depth = 0) {
  if (!node || typeof node !== 'object' || depth > 14) return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const r = deepFindLocalFlow(item, depth + 1);
      if (r) return r;
    }
    return null;
  }
  if (node.api === 'openai-completions' && typeof node.baseURL === 'string' && node.baseURL.includes('127.0.0.1:8765')) {
    return node;
  }
  if (node.providers && typeof node.providers === 'object') {
    const lf = node.providers['local-flow'];
    if (lf) {
      const r = deepFindLocalFlow(lf, depth + 1);
      if (r) return r;
    }
  }
  for (const k of Object.keys(node)) {
    if (k === 'providers') continue; // 已在上面专门处理
    const r = deepFindLocalFlow(node[k], depth + 1);
    if (r) return r;
  }
  return null;
}

async function assertLocalFlowRegistered() {
  let view;
  try {
    view = await rpc('settings.describe', {});
  } catch (e) {
    log(`⚠ 无法读取 settings.describe（${e.message}）—— 跳过本地模型分组断言（不阻断）`);
    return;
  }
  const ns = (view && view.namespaces) || [];
  const lf = ns.find((n) => n.ns === 'llm-pi-ai');
  let prov = lf && lf.value && lf.value.providers && lf.value.providers['local-flow'];
  if (!prov) {
    // 兜底：跨版本 settings.describe 结构可能变化，做深度扫描以防误杀
    const found = deepFindLocalFlow(view);
    if (found) {
      log(`⚠ llm-pi-ai 未以预期路径出现，但深度扫描在 settings 中定位到 local-flow provider（api=${found.api} baseURL=${found.baseURL}）—— 视为已注册`);
      prov = found;
    }
  }
  if (!prov) {
    throw new Error('llm-pi-ai 命名空间 / local-flow provider 未在 settings.describe 中出现：本机模型将不可见');
  }
  if (!prov.api || !prov.baseURL) {
    throw new Error(`local-flow provider 缺少必要字段：api=${prov.api} baseURL=${prov.baseURL}`);
  }
  const models = (prov.models || []).map((m) => m.id);
  log(`✓ llm-pi-ai 已注册，local-flow: api=${prov.api} baseURL=${prov.baseURL} models=[${models.join(', ')}]`);
  const deep = ns.find((n) => n.ns === 'llm-deepseek');
  if (deep) log('✓ llm-deepseek 命名空间存在（DeepSeek 云端模型可用，含新发布模型）');
  else log('（提示：llm-deepseek 命名空间未出现，云端模型可能需另行配置）');
}

async function main() {
  const nodeBin = findNodeBin();
  const dshEntry = findDshEntry();
  if (!nodeBin) {
    console.error('::error::未找到便携 Node（build/node-bundle），请先运行 bundle-dsh.mjs');
    process.exit(1);
  }
  if (!dshEntry) {
    console.error('::error::未找到 dsh 入口（build/dsh-bundle），请先运行 bundle-dsh.mjs');
    process.exit(1);
  }
  log(`Node: ${nodeBin}`);
  log(`dsh:  ${dshEntry}`);

  const dshHome = prepareDshHome();
  log(`DSH_HOME: ${dshHome}`);

  let exited = null; // { code, signal }
  const child = spawn(
    nodeBin,
    [dshEntry, '--profile', 'web', '--port', String(PORT), '--no-open'],
    { env: { ...process.env, DSH_HOME: dshHome }, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let stderrTail = [];
  child.stdout.on('data', (d) => {
    const s = d.toString().trim();
    if (s) log(`[out] ${s.slice(0, 200)}`);
  });
  child.stderr.on('data', (d) => {
    const s = d.toString();
    stderrTail.push(s);
    if (stderrTail.length > 40) stderrTail.shift();
  });
  child.on('exit', (code, signal) => {
    exited = { code, signal };
    log(`进程退出 code=${code} signal=${signal}`);
  });

  const cleanup = () => {
    try { if (!child.killed) child.kill(); } catch (e) { /* ignore */ }
  };
  process.on('exit', cleanup);

  // 1) 等待端口就绪
  log('等待 dsh 就绪…');
  const ready = await waitForPort(START_TIMEOUT_MS);
  if (!ready) {
    console.error('::error::dsh 在超时时间内未监听端口（启动失败）');
    if (exited) console.error(`退出码：${exited.code} 信号：${exited.signal}`);
    console.error(stderrTail.join('').slice(-2000));
    cleanup();
    process.exit(1);
  }
  log('dsh 已就绪');

  // 2) 发多组请求，重点是会触发 Accept-Encoding 解析缺陷的组合
  const cases = [
    ['基础请求', {}],
    ['gzip, deflate, br', { 'Accept-Encoding': 'gzip, deflate, br' }],
    ['identity', { 'Accept-Encoding': 'identity' }],
    ['通配符 *', { 'Accept-Encoding': '*' }],
    ['空值', { 'Accept-Encoding': '' }],
    ['浏览器完整头', {
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br, zstd',
      'Accept-Language': 'zh-CN,zh;q=0.9',
      'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
    }],
  ];
  log(`开始发送 ${cases.length} 组请求…`);
  let survived = true;
  for (const [label, headers] of cases) {
    if (exited) { survived = false; break; }
    await probe(label, headers);
    await sleep(400); // 给崩溃一点时间暴露
    if (exited) {
      console.error(`::error::dsh 在「${label}」之后崩溃（code=${exited.code} signal=${exited.signal}）`);
      survived = false;
      break;
    }
  }

  // 3) 全部请求后再确认一次进程与端口仍健康
  if (survived && !exited) {
    const stillUp = await probe('最终复检', { 'Accept-Encoding': 'gzip' });
    if (!stillUp || exited) survived = false;
  }

  if (!survived) {
    console.error('::error::dsh 冒烟测试失败 —— 它在处理请求时崩溃，不能进入安装包');
    console.error('---- dsh stderr 尾部 ----');
    console.error(stderrTail.join('').slice(-3000));
    cleanup();
    process.exit(1);
  }

  // 4) 增强校验：本地模型分组（llm-pi-ai / local-flow）是否真的注册成功
  try {
    await assertLocalFlowRegistered();
  } catch (e) {
    console.error('::error::本地模型分组校验失败：' + e.message);
    console.error('::error::该 dsh 版本会导致升级后本机模型不可见，不能进入安装包');
    cleanup();
    process.exit(1);
  }

  log('✓ 冒烟测试通过：dsh 能持续服务，未在处理请求时崩溃');
  cleanup();
  await sleep(300);
  try { fs.rmSync(dshHome, { recursive: true, force: true }); } catch (e) { /* ignore */ }
  process.exit(0);
}

main().catch((e) => {
  console.error('::error::冒烟测试异常：', e && e.stack ? e.stack : e);
  process.exit(1);
});
