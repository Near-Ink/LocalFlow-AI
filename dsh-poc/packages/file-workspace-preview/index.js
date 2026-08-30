// file-workspace-preview：把 LocalFlow 的"文件工作区预览"核心价值做成 dsh 工具插件。
// 注册两个模型可直接调用的工具：
//   files_list   —— 列目录（文件/子目录、大小、类型、预览类型）
//   file_preview —— 读取文本/代码/Markdown/HTML 内容做预览；二进制/文档类型返回元信息
// 类型判断复用 LocalFlow artifacts.sniff 的扩展名语义。
import { readdir, stat, readFile } from 'node:fs/promises'
import path from 'node:path'
import { defineTool } from '@deepseek-ai/dsh-tools'

const PREVIEW = {
  // 图片
  '.png': ['image', 'image/png'], '.jpg': ['image', 'image/jpeg'], '.jpeg': ['image', 'image/jpeg'],
  '.gif': ['image', 'image/gif'], '.webp': ['image', 'image/webp'], '.svg': ['image', 'image/svg+xml'], '.bmp': ['image', 'image/bmp'],
  // html
  '.html': ['html', 'text/html'], '.htm': ['html', 'text/html'],
  // 文本/markdown/代码
  '.md': ['markdown', 'text/markdown'], '.markdown': ['markdown', 'text/markdown'],
  '.txt': ['text', 'text/plain'], '.log': ['text', 'text/plain'],
  '.json': ['code', 'application/json'], '.js': ['code', 'text/javascript'], '.jsx': ['code', 'text/javascript'],
  '.ts': ['code', 'text/typescript'], '.tsx': ['code', 'text/typescript'], '.py': ['code', 'text/x-python'],
  '.css': ['code', 'text/css'], '.xml': ['code', 'text/xml'], '.yaml': ['code', 'text/yaml'],
  '.yml': ['code', 'text/yaml'], '.toml': ['code', 'text/toml'], '.sql': ['code', 'text/plain'], '.sh': ['code', 'text/x-sh'],
  // 文档
  '.pdf': ['pdf', 'application/pdf'], '.docx': ['docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  // 音视频
  '.mp3': ['audio', 'audio/mpeg'], '.wav': ['audio', 'audio/wav'],
  '.mp4': ['video', 'video/mp4'], '.webm': ['video', 'video/webm'], '.mov': ['video', 'video/quicktime'],
}

function previewOf(name) {
  const e = path.extname(name).toLowerCase()
  const v = PREVIEW[e]
  return v ? { type: v[0], mime: v[1] } : { type: 'unknown', mime: 'application/octet-stream' }
}

const TEXT_KINDS = new Set(['text', 'markdown', 'code', 'html'])

export const name = 'file-workspace-preview'
export const inject = ['tools']

export function apply(ctx) {
  ctx.tools.register(defineTool({
    name: 'files_list',
    description:
      '列出工作区目录内容：返回该目录下的子目录与文件清单（名称、绝对路径、是否目录、大小、可预览类型）。用于浏览项目文件，如需要再对某文件调用 file_preview。',
    parameters: {
      dir: { type: 'string', required: true, description: '要列出的目录的绝对路径' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_a, v) => [{ type: 'text', text: `目录 ${v.dir}: ${v.entries ? v.entries.length : 0} 项` }],
    },
    async execute(args, exec) {
      let st
      try { st = await stat(args.dir) } catch (e) { return { dir: args.dir, error: `无法访问: ${e.message}` } }
      if (!st.isDirectory()) return { dir: args.dir, error: '路径不是目录' }
      let items = await readdir(args.dir, { withFileTypes: true }).catch((e) => { throw new Error(`readdir 失败: ${e.message}`) })
      items = items.slice(0, 500)
      const entries = []
      for (const it of items) {
        const full = path.join(args.dir, it.name)
        const isDir = it.isDirectory()
        let size = 0
        if (!isDir) { try { size = (await stat(full)).size } catch {} }
        entries.push({ name: it.name, path: full, isDir, size, preview: isDir ? null : previewOf(it.name).type })
      }
      return { dir: args.dir, total: entries.length, entries }
    },
  }))

  ctx.tools.register(defineTool({
    name: 'file_preview',
    description:
      '预览一个文件：文本/Markdown/代码/HTML 返回其正文内容（默认前 200 行）；图片/PDF/音视频等二进制类型返回文件元信息与预览类型，不返回内容。用于查看项目文件内容。',
    parameters: {
      path: { type: 'string', required: true, description: '要预览的文件绝对路径' },
      limit: { type: 'number', description: '最多返回行数，默认 200' },
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_a, v) => [{ type: 'text', text: v.content != null ? `预览 ${v.path}（${v.lineCount ?? ''}行）` : `<${v.type}> ${v.path}` }],
    },
    async execute(args, exec) {
      const p = previewOf(args.path)
      const lim = args.limit || 200
      let st
      try { st = await stat(args.path) } catch (e) { return { path: args.path, error: `无法访问: ${e.message}` } }
      if (st.isDirectory()) return { path: args.path, error: '是目录，请用 files_list 列出' }
      if (!TEXT_KINDS.has(p.type)) {
        return {
          path: args.path, type: p.type, mime: p.mime, size: st.size,
          previewable: false, content: null,
          note: `${p.type} 类型（${p.mime}）为二进制/文档，不支持直接文本预览；请用适合的打开方式查看`,
        }
      }
      let raw
      try { raw = await readFile(args.path, 'utf8') } catch (e) { return { path: args.path, error: `读取失败: ${e.message}` } }
      const lines = raw.split('\n')
      const truncated = lines.length > lim
      const content = lines.slice(0, lim).join('\n')
      return { path: args.path, type: p.type, mime: p.mime, size: st.size, truncated, lineCount: lines.length, content }
    },
  }))
}