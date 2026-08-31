"""Ollama 引擎适配器 — MVP 默认本地推理后端

通过 HTTP 与本地 Ollama 服务通信。
GPU/显存细节由 Ollama 自己处理，我们只做接口适配。
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List, Optional

import httpx

from ..ports.cache import CacheEngine
from ..ports.engine import (
    ChatMessage,
    ChatResponse,
    GenerateOptions,
    LLMEngine,
)


def _normalize_tool_calls(raw_calls) -> Optional[list]:
    """把后端原始 tool_calls 归一化为稳定结构 [{id,name,arguments(dict)}]"""
    if not raw_calls:
        return None
    result = []
    for i, c in enumerate(raw_calls):
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        result.append({
            "id": c.get("id") or f"call_{i}",
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return result or None


class OllamaEngine(LLMEngine):
    """Ollama 本地推理引擎适配器"""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)
        self._vision_cache: dict = {}  # model -> bool|None?；TTL 简单缓存
        self._tools_cache: dict = {}  # model -> bool（是否支持 function calling）
        self.cache: Optional["CacheEngine"] = None  # 可选：接 app.cache 做 chat 结果缓存

    async def health(self) -> bool:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags")
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[dict]:
        try:
            r = await self._client.get(f"{self.base_url}/api/tags")
            if r.status_code != 200:
                return []
            data = r.json()
            models = []
            for m in data.get("models", []):
                size = m.get("size", 0)
                size_gb = round(size / (1024**3), 2)
                models.append({
                    "id": m.get("name", ""),
                    "name": m.get("name", ""),
                    "size": size,
                    "size_gb": size_gb,
                    "modified_at": m.get("modified_at", ""),
                })
            return models
        except Exception:
            return []

    async def get_context_length(self, model: str) -> int:
        """查询模型上下文窗口大小（Ollama /api/show）"""
        try:
            r = await self._client.post(
                f"{self.base_url}/api/show", json={"name": model}
            )
            if r.status_code != 200:
                return 0
            data = r.json()
            model_info = data.get("model_info", {}) or {}
            params = data.get("parameters", "") or ""
            # 优先：新版 Ollama 在 model_info 暴露 *.context_length
            for k, v in model_info.items():
                if k.endswith(".context_length") and isinstance(v, int):
                    return v
            # 退化：从 parameters 文本解析 num_ctx（形如 "num_ctx 4096"）
            import re
            m = re.search(r"num_ctx\D*?(\d+)", str(params))
            if m:
                return int(m.group(1))
            return 0
        except Exception:
            return 0

    async def supports_vision(self, model: str) -> bool:
        """检测模型是否支持视觉（多模态看图）。结果缓存，避免反复 /api/show"""
        cached = self._vision_cache.get(model)
        if cached is not None:
            return cached
        vision = False
        try:
            r = await self._client.post(
                f"{self.base_url}/api/show", json={"name": model}
            )
            if r.status_code == 200:
                caps = (r.json().get("capabilities") or [])
                vision = any(str(c).lower() == "vision" for c in caps)
        except Exception:
            vision = False
        self._vision_cache[model] = vision
        return vision

    async def supports_tools(self, model: str) -> bool:
        """检测模型是否支持原生函数调用（tools）。不支持则 Agent 走文本 ReAct 降级"""
        cached = self._tools_cache.get(model)
        if cached is not None:
            return cached
        tools_ok = False
        try:
            r = await self._client.post(
                f"{self.base_url}/api/show", json={"name": model}
            )
            if r.status_code == 200:
                caps = (r.json().get("capabilities") or [])
                tools_ok = any(str(c).lower() == "tools" for c in caps)
        except Exception:
            tools_ok = False
        self._tools_cache[model] = tools_ok
        return tools_ok

    async def pull(self, model: str) -> dict:
        """拉取模型到本地（阻塞等待完成；大模型耗时久，用独立长超时连接）"""
        # 用独立连接避免共享 client 的 120s 超时打断大模型下载
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json=self._pull_body(model),
            ) as resp:
                last = {"status": "error", "error": f"HTTP {resp.status_code}"}
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if last.get("done"):
                        return {"status": "done", "model": model, "detail": last}
                    if "error" in last:
                        return {"status": "error", "model": model, "error": last.get("error")}
                return last

    async def pull_stream(self, model: str):
        """流式拉取模型（后台任务用），逐行产出进度事件。

        产出事件结构：{"status": ...}（非下载态）或
        {"status":"downloading","total":N,"completed":N,"digest":...}，
        完成时 yield {"status":"success"}，出错 yield {"status":"error","error":...}。
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(None)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json=self._pull_body(model),
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace")[:300]
                    yield {"status": "error", "error": f"HTTP {resp.status_code}: {body}"}
                    return
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in obj:
                        yield {"status": "error", "error": obj.get("error")}
                        return
                    if obj.get("done"):
                        yield {"status": "success", "detail": obj}
                        return
                    yield {
                        "status": obj.get("status", "progress"),
                        "total": obj.get("total") or 0,
                        "completed": obj.get("completed") or 0,
                        "digest": obj.get("digest") or "",
                    }
                else:
                    # 流在未产出生完成/错误标志前被远端断开 → 可续传
                    yield {"status": "error", "error": "连接中断：下载未完成（重新拉取可自动续传）"}

    @staticmethod
    def _pull_body(model: str) -> dict:
        # 兼容不同 Ollama 版本：新版用 name，老客户端用 model；两个字段都送上最稳
        return {"name": model, "model": model}

    async def chat(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> ChatResponse:
        opts = options or GenerateOptions()
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "stream": False,
            "options": self._build_opts(opts),
        }
        if opts.tools:
            payload["tools"] = opts.tools
        # 结果缓存（接 app.cache 时生效；tools 调用不缓存，避免污染）
        cache_key = None
        if self.cache is not None and not opts.tools:
            cache_key = self._chat_cache_key(model, messages, opts)
            try:
                hit = await self.cache.get(cache_key, namespace="llm")
                if hit is not None:
                    import json as _json
                    cached = _json.loads(hit.value)
                    return ChatResponse(
                        content=cached.get("content", ""),
                        model=model,
                        usage=cached.get("usage", {}) or {},
                        finish_reason="stop",
                        tool_calls=None,
                        raw={},
                    )
            except Exception:
                pass
        r = await self._client.post(f"{self.base_url}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {})
        if cache_key is not None:
            try:
                import json as _json
                await self.cache.set(
                    cache_key,
                    _json.dumps({
                        "content": msg.get("content", ""),
                        "usage": {
                            "prompt_tokens": data.get("prompt_eval_count", 0),
                            "completion_tokens": data.get("eval_count", 0),
                            "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
                        },
                    }),
                    namespace="llm",
                    ttl=3600,
                )
            except Exception:
                pass
        return ChatResponse(
            content=msg.get("content", ""),
            model=model,
            usage={
                "prompt_tokens": data.get("prompt_eval_count", 0),
                "completion_tokens": data.get("eval_count", 0),
                "total_tokens": data.get("prompt_eval_count", 0) + data.get("eval_count", 0),
            },
            finish_reason="stop",
            tool_calls=_normalize_tool_calls(msg.get("tool_calls")),
            raw=data,
        )

    def _chat_cache_key(self, model: str, messages: List[ChatMessage], opts: GenerateOptions) -> str:
        """相同 (model, messages, options) 视为同一请求，缓存其完整回复。"""
        import hashlib
        import json as _json
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "opts": self._build_opts(opts),
            "tools": bool(opts.tools),
        }
        blob = _json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    async def chat_stream(
        self,
        model: str,
        messages: List[ChatMessage],
        options: Optional[GenerateOptions] = None,
    ) -> AsyncIterator[str]:
        opts = options or GenerateOptions(stream=True)
        payload = {
            "model": model,
            "messages": [self._msg_to_dict(m) for m in messages],
            "stream": True,
            "options": self._build_opts(opts),
        }
        async with self._client.stream("POST", f"{self.base_url}/api/chat", json=payload) as resp:
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message", {})
                chunk = msg.get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    break

    # --- helpers ---

    @staticmethod
    def _msg_to_dict(msg: ChatMessage) -> dict:
        d = {"role": msg.role, "content": msg.content}
        if msg.name:
            d["name"] = msg.name
        if msg.images:
            # Ollama images 字段需要纯 base64（去掉 data URI 的前缀）
            d["images"] = [img.split(",", 1)[1] if "," in img else img for img in msg.images]
        if msg.tool_calls:
            # agent 层用稳定结构 {id,name,arguments(dict)}；Ollama 转成嵌套 wire 格式
            d["tool_calls"] = [
                {"id": c.get("id"), "function": {
                    "name": c.get("name", ""),
                    "arguments": c.get("arguments", {}),
                }}
                for c in msg.tool_calls if isinstance(c, dict)
            ]
        return d

    def _build_opts(self, opts: GenerateOptions) -> dict:
        d = {}
        if opts.temperature is not None:
            d["temperature"] = opts.temperature
        if opts.top_p is not None:
            d["top_p"] = opts.top_p
        if opts.max_tokens is not None:
            d["num_predict"] = opts.max_tokens
        if opts.stop:
            d["stop"] = opts.stop
        return d