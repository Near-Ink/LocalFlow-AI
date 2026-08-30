"""附件解析 API：把 PDF / DOCX 解析成文本给 AI 读取"""

import base64
import io
import re
import zipfile

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/attach", tags=["attach"])


class ParseRequest(BaseModel):
    name: str
    data_b64: str  # 原始字节的 base64


def _parse_docx(data: bytes) -> str:
    """DOCX 本质是 zip，读取 word/document.xml 中所有 <w:t> 文本，按段落换行"""
    z = zipfile.ZipFile(io.BytesIO(data))
    xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
    paras = re.findall(r"<w:p(?:\s[^>]*)?>.*?</w:p>", xml, re.S) or [xml]
    out = []
    for p in paras:
        t = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p))
        out.append(t)
    return "\n".join(out).strip()


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        t = (page.extract_text() or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


@router.post("/parse")
async def parse_attach(req: ParseRequest):
    try:
        data = base64.b64decode(req.data_b64 or "")
    except Exception as e:
        return {"ok": False, "msg": "Base64 解码失败：" + str(e)}
    name = (req.name or "").lower()
    try:
        if name.endswith(".docx"):
            text = _parse_docx(data)
            if text:
                return {"ok": True, "text": text}
            return {"ok": False, "msg": "Word 文档无可提取文本"}
        if name.endswith(".pdf"):
            text = _parse_pdf(data)
            if text:
                return {"ok": True, "text": text}
            return {"ok": False, "msg": "PDF 无可提取文本（可能是扫描件 / 图片型 PDF）"}
        # 其余文本类由前端直接读取，这里兜底返回提示
        return {"ok": False, "msg": "暂不支持解析该类型：" + name}
    except Exception as e:
        return {"ok": False, "msg": "解析失败：" + str(e)}