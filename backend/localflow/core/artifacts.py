"""产物(artifact)元信息识别：按文件扩展名嗅探可预览类型

工具产生文件后，在返回的 data 里带 artifact，前端据此在右侧渲染实时预览：
- image   -> 后端读 bytes 转 base64，前端 <img> 大图
- html    -> 后端给全文，前端 iframe srcdoc 实时渲染
- markdown/text/code -> 前端文本展示
- pdf     -> 前端给下载/查看提示
"""

from __future__ import annotations

from pathlib import Path

_TYPES: dict[str, str] = {
    # 图片
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".svg": "image",
    # html
    ".html": "html", ".htm": "html",
    # 文本/Markdown/代码
    ".md": "markdown", ".markdown": "markdown",
    ".txt": "text", ".text": "text", ".log": "text",
    ".json": "code", ".js": "code", ".jsx": "code", ".ts": "code", ".tsx": "code",
    ".py": "code", ".css": "code", ".xml": "code", ".yaml": "code", ".yml": "code",
    ".toml": "code", ".sql": "code", ".sh": "code",
    # 文档
    ".pdf": "pdf", ".docx": "docx",
    # 音视频
    ".mp3": "audio", ".wav": "audio", ".mp4": "video", ".webm": "video", ".mov": "video",
}

_MIME: dict[str, str] = {
    "image": "image/png", "html": "text/html", "markdown": "text/markdown",
    "text": "text/plain", "code": "text/plain", "pdf": "application/pdf",
    "audio": "audio/mpeg", "video": "video/mp4",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def sniff(path: str | Path) -> dict | None:
    """按扩展名返回产物元信息；无法识别返回 None"""
    p = Path(path)
    name = p.name
    ext = p.suffix.lower()
    atype = _TYPES.get(ext)
    if atype is None:
        return None
    return {
        "type": atype,
        "name": name,
        "path": str(p),
        "mime": _MIME.get(atype, "application/octet-stream"),
    }