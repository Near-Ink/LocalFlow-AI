#!/usr/bin/env node
/**
 * LocalFlow → dsh 本地部署模型同步器
 *
 * 把 LocalFlow（Ollama）真实已安装的模型，动态同步进 DeepSeek Harness 的
 * `local-flow` provider 模型列表，让 dsh 输入区的模型选择器实时出现新模型，
 * 全程无需重启 dsh（通过 settings.update 深合并 + llm 目录实时刷新）。
 *
 * 用法:
 *   node sync-localflow-models.mjs              # 同步一次
 *   node sync-localflow-models.mjs --watch      # 每 60s 同步一次（常驻）
 *   node sync-localflow-models.mjs --interval 300 --watch
 *
 * 可选环境变量:
 *   LOCALFLOW_API        LocalFlow 后端地址（默认 http://127.0.0.1:8765）
 *   DSH_WEB              dsh Web 地址（默认 http://127.0.0.1:8080）
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const LOCALFLOW_API = process.env.LOCALFLOW_API || 'http://127.0.0.1:8765';
const DSH_WEB = process.env.DSH_WEB || 'http://127.0.0.1:8080';
const DEFAULT_CONTEXT = 32768;
const DEFAULT_MAX_TOKENS = 4096;

/** 调 dsh 的 unary RPC（HTTP JSON 封装）。 */
async function dshRpc(method, payload) {
  const rpcId = crypto.randomUUID();
  const res = await fetch(`${DSH_WEB}/api/${method}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ type: 'client-request', rpcId, method, payload }),
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`dsh ${method} HTTP ${res.status}`);
  const data = await res.json();
  if (!data.result?.ok) {
    throw new Error(`dsh ${method} 失败: ${data.result?.error?.code}: ${data.result?.error?.message}`);
  }
  return data.result.value;
}

/** 从 LocalFlow 拉真实模型（Ollama 已装模型）。 */
async function fetchLocalflowModels() {
  const res = await fetch(`${LOCALFLOW_API}/api/models`, {
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`LocalFlow /api/models HTTP ${res.status}`);
  const list = await res.json();
  return list.map((m) => ({
    id: String(m.id ?? m.name ?? ''),
    vision: Boolean(m.vision),
  })).filter((m) => m.id.length > 0);
}

/** 读 dsh 现有 local-flow provider 配置（含 models）。 */
async function fetchLocalFlowProvider() {
  const view = await dshRpc('settings.describe', {});
  const ns = view.namespaces.find((n) => n.ns === 'llm-pi-ai');
  if (!ns) throw new Error('dsh 未注册 llm-pi-ai 命名空间');
  const provider = ns.value?.providers?.['local-flow'];
  if (!provider) throw new Error('dsh 未注册 local-flow provider（请用 --patch ./patch-localflow.yml 启动）');
  return { provider, revision: ns.revision };
}

/** 合并：真实模型为准，保留已有元数据，新模型给默认。 */
function mergeModels(real, existing) {
  const byId = new Map(existing.map((m) => [m.id, m]));
  return real.map(({ id, vision }) => {
    const old = byId.get(id);
    if (old) return old; // 保留原有配置（contextWindow / maxTokens / name 等）
    return {
      id,
      name: `LocalFlow ${id}`,
      contextWindow: DEFAULT_CONTEXT,
      maxTokens: DEFAULT_MAX_TOKENS,
      input: [],
      compat: { chatTemplateKwargs: {} },
    };
  });
}

async function syncOnce() {
  const [real, { provider }] = await Promise.all([fetchLocalflowModels(), fetchLocalFlowProvider()]);
  const existing = Array.isArray(provider.models) ? provider.models : [];
  const next = mergeModels(real, existing);

  const added = next.filter((m) => !existing.some((e) => e.id === m.id));
  const removed = existing.filter((e) => !real.some((r) => r.id === e.id));
  const same =
    added.length === 0 && removed.length === 0 &&
    JSON.stringify(next) === JSON.stringify(existing);

  if (same) {
    console.log(`[sync] 无变化：${next.length} 个本地模型已同步`);
    return { changed: false, count: next.length };
  }

  await dshRpc('settings.update', {
    ns: 'llm-pi-ai',
    patch: { providers: { 'local-flow': { models: next } } },
  });
  console.log(`[sync] 已同步 ${next.length} 个本地模型到 dsh`);
  if (added.length) console.log(`  ➕ 新增: ${added.map((m) => m.id).join(', ')}`);
  if (removed.length) console.log(`  ➖ 移除: ${removed.map((m) => m.id).join(', ')}`);
  return { changed: true, count: next.length };
}

// ── 入口 ──────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const watch = args.includes('--watch');
const ivIdx = args.indexOf('--interval');
const interval = ivIdx >= 0 ? Math.max(10, Number(args[ivIdx + 1]) || 60) : 60;

const __dirname = dirname(fileURLToPath(import.meta.url));
const README = join(__dirname, 'sync-localflow-models.md');

if (watch) {
  console.log(`[sync] 常驻模式：每 ${interval}s 同步一次（Ctrl+C 退出）`);
  const tick = async () => { try { await syncOnce(); } catch (e) { console.error(`[sync] ${e.message}`); } };
  await tick();
  setInterval(tick, interval * 1000);
} else {
  try {
    await syncOnce();
    console.log(`[sync] 说明见 ${README}`);
  } catch (e) {
    console.error(`[sync] ${e.message}`);
    process.exit(1);
  }
}
